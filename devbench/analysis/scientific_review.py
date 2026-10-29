"""Research assertions and scientific-review records over verified local evidence.

These sidecar records do not modify sealed analyses, fetch publications, invoke
models, grant execution, authenticate a reviewer or establish scientific truth.
"""
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from .checkpoints import digest
from .episode_proposals import (_absolute, _authoring_records, _bind_manifest_snapshot,
    _load, _retained, _seal, require_disjoint_output)
from .episodes import _json_copy, _public_only
from devbench.study.evidence import _read, decode_json

_SCOPE = "research_assertions_and_scientific_review_not_execution_approval"
_SECTIONS = {"configuration", "admission", "design", "provenance", "runtime",
             "numerical_results", "diagnostics", "limitations", "execution"}
_AUTHORITY = {"clinical_release_allowed": False, "data_execution_granted": False,
              "model_execution_granted": False}


def prepare_claim_packet(output, packet, *, allow_evidence_read=False, read_only_paths=()):
    """Seal a versioned claim companion without editing any legacy scientific packet."""
    from .scientific_judgment import parse_claim_packet, verified_artifacts
    if allow_evidence_read is not True:
        raise PermissionError('Claim packet preparation requires evidence-reading authorization')
    packet=parse_claim_packet(packet)
    verified_artifacts(packet)
    output=require_disjoint_output(_path(output), [*read_only_paths,*[e.path for e in packet.evidence]])
    return _seal(output,'claim-packet.json',packet.model_dump(mode='json'),
        'analysis.claim_packet',scope='claim_evidence_not_scientific_acceptance')


def load_claim_packet(manifest, expected_root, *, allow_evidence_read=False):
    from .scientific_judgment import parse_claim_packet, verified_artifacts
    if allow_evidence_read is not True:
        raise PermissionError('Claim packet verification requires evidence-reading authorization')
    _,record,_=_load(_path(manifest),expected_root,'claim-packet.json')
    packet=parse_claim_packet(record)
    verified_artifacts(packet)
    return packet


def record_claim_judgment(output, packet_manifest, *, expected_root, judgment,
                          allow_evidence_read=False, allow_review=False):
    from .scientific_judgment import validate_judgment
    if allow_review is not True:
        raise PermissionError('Claim review recording requires review authorization')
    packet=load_claim_packet(packet_manifest,expected_root,allow_evidence_read=allow_evidence_read)
    dimensions=validate_judgment(packet,judgment)
    output=require_disjoint_output(_path(output),[_path(packet_manifest).parent.parent,*[e.path for e in packet.evidence]])
    record={'format':'devbench.claim-judgment-receipt/'+packet.record_format.rsplit('/',1)[1],'packet_root':expected_root,
        'judgment':judgment.model_dump(mode='json'),'dimensions':dimensions,
        'human_authorship_authenticated':False,'scientific_validity_established':False,**_AUTHORITY}
    return _seal(output,'claim-judgment.json',record,'analysis.claim_judgment',scope=_SCOPE)


def load_claim_judgment(manifest, expected_root, *, packet_manifest, packet_root, allow_evidence_read=False):
    from .scientific_judgment import parse_claim_judgment, validate_judgment
    packet=load_claim_packet(packet_manifest,packet_root,allow_evidence_read=allow_evidence_read)
    _,record,_=_load(_path(manifest),expected_root,'claim-judgment.json')
    _keys(record,{'format','packet_root','judgment','dimensions','human_authorship_authenticated',
        'scientific_validity_established',*_AUTHORITY})
    if (record['format']!='devbench.claim-judgment-receipt/'+packet.record_format.rsplit('/',1)[1] or record['packet_root']!=packet_root
            or record['human_authorship_authenticated'] is not False
            or record['scientific_validity_established'] is not False
            or any(record[key] is not value for key,value in _AUTHORITY.items())):
        raise ValueError('Invalid claim review authority or packet binding')
    judgment=parse_claim_judgment(record['judgment'])
    if record['dimensions']!=validate_judgment(packet,judgment):
        raise ValueError('Claim review dimensions do not match coordinator evidence')
    return judgment


def _keys(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError("Use exactly the declared research/review fields")


def _text(value, limit=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("Use bounded nonempty research/review text")
    return value


def _identifier(value):
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,79}", value) is None:
        raise ValueError("Use bounded research claim/source identifiers")
    return value


def _path(value):
    if not isinstance(value, (str, Path)):
        raise ValueError("Use an absolute evidence path")
    return _absolute(value)


def _list(value, limit):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError("Research/review list exceeds its bound")
    return value


def _research(value):
    _keys(value, {"researcher_label", "sources", "claims"})
    _text(value["researcher_label"], 200)
    sources, claims = set(), set()
    for source in _list(value["sources"], 64):
        _keys(source, {"source_id", "title", "url", "version", "access", "accessed_on", "limitations"})
        key = _identifier(source["source_id"])
        if key in sources:
            raise ValueError("Duplicate research source identifier")
        sources.add(key)
        _text(source["title"], 500)
        url = urlsplit(_text(source["url"], 2000))
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("Source URLs must be credential-free HTTPS references")
        if source["version"] is not None:
            _text(source["version"], 200)
        if _text(source["access"], 30) not in {"documentation", "full_text", "abstract", "metadata"}:
            raise ValueError("Declare the actual source access level")
        date.fromisoformat(_text(source["accessed_on"], 10))
        _text(source["limitations"])
    for claim in _list(value["claims"], 128):
        _keys(claim, {"claim_id", "text", "basis", "source_ids", "subject_sections", "applicability", "limitations"})
        key = _identifier(claim["claim_id"])
        if key in claims:
            raise ValueError("Duplicate research claim identifier")
        claims.add(key)
        if _text(claim["basis"], 30) not in {"source_statement", "inference"}:
            raise ValueError("Separate source statements from research inference")
        references = _list(claim["source_ids"], 64)
        sections = _list(claim["subject_sections"], len(_SECTIONS))
        if (any(not isinstance(item, str) for item in references + sections)
                or len(set(references)) != len(references) or not set(references) <= sources
                or not sections or len(set(sections)) != len(sections) or not set(sections) <= _SECTIONS
                or (claim["basis"] == "source_statement" and not references)):
            raise ValueError("Research claims must reference known sources and packet sections")
        for field in ("text", "applicability", "limitations"):
            _text(claim[field])
    return _json_copy(value)


def _bound_json(root, name, expected, limit=1_000_000):
    content = _read(_absolute(root / name), limit)
    if hashlib.sha256(content).hexdigest() != expected:
        raise ValueError("Review subject changed after verification: " + name)
    return decode_json(content)


def _proposal_subject(path, expected_root):
    root, record, _ = _load(path, expected_root, "proposal.json")
    context = None
    if record.get("format") == "devbench.episode-proposal/1":
        from .episode_proposals import _verify_authoring
        inputs = list(record["input_paths"].values())
        source = record["source_sha256"]
        if "authoring" in record:
            _verify_authoring(record["authoring"], record["configuration"], record["input_identity"], source)
        provenance = record["input_identity"]
    elif record.get("format") == "devbench.workbench-proposal/1":
        from .workbench_proposals import _verify_workbench_authoring
        inputs = [record["external_input"]["path"]]
        if record["model_input"] is not None:
            inputs.append(record["model_input"]["path"])
        source = record["source_bundle_sha256"]
        if "authoring" in record:
            _verify_workbench_authoring(record, record["authoring"])
        provenance = {"external_input": record["external_input"], "model_input": record["model_input"]}
    else:
        raise ValueError("Unsupported proposal format for scientific review")
    subject = {"kind": "proposal", "root_hash": expected_root, "record_sha256": digest(record),
        "source_identity": source, "verification_scope": "sealed_proposal_and_available_authoring_records"}
    if "authoring" in record:
        context = _authoring_records(record["authoring"])["context.json"]
        subject["authoring_root"] = record["authoring"]["root_hash"]
        inputs.append(str(_absolute(record["authoring"]["manifest_path"]).parent.parent))
    observed = (context or {}).get("observed", {})
    sections = {"configuration": record["configuration"], "provenance": provenance,
        "admission": (context or {}).get("admission", {"status": "not_recorded"}),
        "design": observed.get("design_summary", {"status": "not_recorded"}),
        "runtime": record.get("runtime", {"status": "not_captured_in_packet"}),
        "numerical_results": {"status": "not_executed"}, "diagnostics": observed,
        "execution": {"status": "not_executed"},
        "limitations": ["Proposal inspection is not scientific approval or full design validation.",
                        "Current input contents and original authoring logs are not copied into this packet."]}
    return root, subject, sections, inputs


def _episode_subject(root, expected_root):
    from .episode_synthesis import verify_synthesis
    verified = verify_synthesis(root, expected_root, allow_evidence_read=True)
    manifest = decode_json(_read(root / "audit/manifest.json", 16_000_000))
    _bind_manifest_snapshot(manifest, expected_root)
    index_hash = next(a["sha256"] for a in manifest["artifacts"] if a["name"] == "artifact-index.json")
    index = _bound_json(root, "artifact-index.json", index_hash, 16_000_000)
    config, report, runtime, snapshot = (_bound_json(root, name, index[name])
        for name in ("parameters.json", "synthesis.json", "runtime.json", "snapshot.json"))
    binding = _bound_json(root, "review-binding.json", index["review-binding.json"]) if "review-binding.json" in index else {}
    subject = {"kind": "episode", "root_hash": expected_root, "source_identity": config["source_sha256"],
        "snapshot_id": config["snapshot_id"], "verification_scope": verified["scope"],
        "numerical_report_evidence_verified": verified["evidence_verified"], "proposal_root": binding.get("proposal_root")}
    return root, subject, {"configuration": config, "provenance": snapshot,
        "admission": {"status": "refer_to_bound_proposal"}, "design": report["targets"], "runtime": runtime,
        "numerical_results": report, "diagnostics": report["tool_events"],
        "execution": {key: report[key] for key in ("outcome", "reason", "harness_accepted", "usage")},
        "limitations": ["Report consistency is not independent scientific validity.",
                        "Full producer tables and input matrices remain in the original episode."]}, []


def _workbench_subject(root, expected_root):
    from .bundles import verify_bundle
    verified = verify_bundle(root, expected_root)
    manifest = _bound_json(root, "receipts.json", expected_root, 16_000_000)
    def artifact(name):
        return _bound_json(root, name, manifest["artifacts"][name]) if name in manifest["artifacts"] else {"status": "not_recorded"}
    config, report = artifact("parameters.json"), artifact("report.json")
    binding, proposal = artifact("review-binding.json"), artifact("plan-review/proposal/proposal.json")
    reviewed_config = proposal.get("configuration")
    configuration = {"question": (reviewed_config or {}).get("question", config.get("prompt")),
        "parameters": config, "reviewed_configuration": reviewed_config}
    # Mutable external bindings are used only to protect their destinations
    # from writes. They do not establish current external-input identity.
    bindings = decode_json(_read(_absolute(root / "bindings.json"), 1_000_000)) if (root / "bindings.json").exists() else {}
    inputs = [str(_path(bindings["data"]))] if "data" in bindings else []
    return root, {"kind": "workbench", "root_hash": expected_root, "source_identity": manifest["source_sha256"],
        "data_sha256": manifest["data_sha256"], "verification_scope": verified["scope"],
        "proposal_root": binding.get("proposal_root")}, {
        "configuration": configuration, "provenance": {"data_sha256": manifest["data_sha256"], "external_input_rechecked": False},
        "admission": {"status": "refer_to_bound_proposal"}, "design": {"status": "not_a_condition_contrast"},
        "runtime": artifact("runtime.json"), "numerical_results": report,
        "diagnostics": artifact("reviewer-report.json"), "execution": artifact("measurements.json"),
        "limitations": ["Native artifact integrity is not numerical replay or biological validation.",
                        "External inputs remain external; models and figures remain in the original bundle."]}, inputs


def _join_proposal_evidence(subject, sections, manifest, expected_root):
    directory, packet = _packet(manifest, expected_root)
    prior = packet["subject"]
    if prior.get("kind") != "proposal":
        raise ValueError("Join a proposal packet, not another result packet")
    if subject.get("proposal_root") != prior["root_hash"]:
        raise ValueError("Proposal packet does not bind the completed analysis")
    subject["proposal_packet_root"] = expected_root
    sections["admission"] = {**_json_copy(packet["sections"]["admission"]),
                             "evidence_time": "proposal_before_execution"}
    sections["design"] = {"proposal_observations": _json_copy(packet["sections"]["design"]),
                          "execution": sections["design"]}
    sections["limitations"].append(
        "Joined admission/design observations were recorded before execution; they are not a new post-execution validation.")
    return [directory, *packet["subject"].get("read_only_paths", [])]


def _method_context(value):
    """Validate the bounded, captured context without granting scientific authority."""
    if value is None:
        return None
    value = _json_copy(value)
    if len(json.dumps(value, allow_nan=False).encode()) > 128 * 1024:
        raise ValueError("Method context exceeds the reviewer projection bound")
    _keys(value, {"format", "status", "question", "mode", "estimand", "requirements", "methods", "input_design", "omitted_evidence", "scope"})
    if (value["format"] != "devbench.rna-assessment-method-context/2" or value["status"] not in {"available", "not_assessed"}
            or value["scope"] != "retained_method_context_not_independent_scientific_validation"):
        raise ValueError("Unsupported retained method context")
    _keys(value["question"], {"text", "source_sha256"})
    if value["question"]["text"] is not None:
        _text(value["question"]["text"], limit=8000)
    _retained(value["question"]["source_sha256"])
    from .rna_publication_profiles import MODES, ESTIMAND_SCHEMA
    if value["mode"] not in MODES:
        raise ValueError("Unknown frozen assessment mode")
    if value["estimand"] is not None:
        from copy import deepcopy
        from jsonschema import validate, ValidationError
        schema = deepcopy(ESTIMAND_SCHEMA)
        reference_schema = schema["properties"]["requirement_sources"]["items"]
        reference_schema["properties"].pop("record_ref")
        reference_schema["properties"]["record_sha256"] = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
        reference_schema["required"] = ["origin", "requirement_ids", "record_sha256"]
        try:
            validate(value["estimand"], schema)
        except ValidationError as error:
            raise ValueError("Malformed captured estimand context") from error
        for reference in value["estimand"]["requirement_sources"]:
            _keys(reference, {"origin", "requirement_ids", "record_sha256"})
            _retained(reference["record_sha256"])
    if any(type(value[key]) is not list or len(value[key]) > 512 for key in ("requirements", "methods", "omitted_evidence")):
        raise ValueError("Method context requires bounded descriptor lists")
    for requirement in value["requirements"]:
        _keys(requirement, {"requirement_id", "origin", "name", "value", "evidence", "record_sha256"})
        _retained(requirement["record_sha256"])
        for key in ("requirement_id", "origin", "name", "evidence"):
            _text(requirement[key], limit=16000)
    for method in value["methods"]:
        _keys(method, {"method_version_id", "method_id", "name", "step_id", "parameters", "omitted_parameters"})
        if type(method["parameters"]) is not dict or type(method["omitted_parameters"]) is not list:
            raise ValueError("Method context requires retained parameter projections")
        for key in ("method_version_id", "method_id", "name", "step_id"):
            _text(method[key])
        for parameter in method["omitted_parameters"]:
            _text(parameter)
    if value["input_design"] is not None:
        _keys(value["input_design"], {"representation", "metadata_roles", "design", "audit"})
        if any(type(value["input_design"][key]) is not dict for key in ("metadata_roles", "design", "audit")):
            raise ValueError("Method context requires retained design and audit objects")
    for omitted in value["omitted_evidence"]:
        _keys(omitted, {"kind", "reason", "count"})
        if omitted["kind"] != "method_context" or type(omitted["count"]) is not int or omitted["count"] < 1:
            raise ValueError("Invalid omitted method context")
    _public_only(value)
    return value


def _method_context_available(value):
    return (isinstance(value, dict) and value.get("status") == "available" and bool(value.get("question", {}).get("text"))
        and isinstance(value.get("estimand"), dict) and bool(value.get("methods")) and isinstance(value.get("input_design"), dict))


def _assessment_subject(path, expected_root, *, workspace=None, artifact_service=None):
    from .rna_publication_report import load_assessment_report
    report = load_assessment_report(path, expected_root, allow_evidence_read=True,
        workspace=workspace, artifact_service=artifact_service)
    evidence = report["evidence"]
    root = path.parent.parent
    reverified = workspace is not None and artifact_service is not None
    subject = {"kind": report["subject"]["kind"], "root_hash": expected_root,
        "record_sha256": digest(report), "verification_scope": "sealed_recorded_assessment_report",
        "comparison_source_reverified": reverified, "assessment_subject": report["subject"]}
    inputs = []
    for reference in report["subject"].values():
        if isinstance(reference, dict):
            inputs.append(str(_path(reference["manifest_path"]).parent.parent if "manifest_path" in reference else _path(reference["path"])))
    sections = {"configuration": {"question": None, "question_status": "retained_in_bound_assessment_intent",
            "assessment_subject_kind": report["subject"]["kind"]},
        "admission": {"status": "review_not_execution_admission"},
        "design": {"diagnostics": evidence["diagnostics"], "tested_families": evidence["tested_families"]},
        "provenance": {"comparison_source_reverified": reverified, "assessment_subject": report["subject"]},
        "runtime": {"status": "refer_to_retained_output_bundle"},
        "numerical_results": {"format": report["format"], "metric_results": evidence["metric_results"],
            "numeric_assessments": evidence["numeric_assessments"]},
        "diagnostics": evidence["diagnostics"], "execution": evidence["execution"],
        "limitations": ["Recorded comparison states are separate from independent scientific qualification.",
            "Reviewers cannot change deterministic metrics, waive failed diagnostics or grant source access."]}
    assessment = {"artifact_registry": evidence["artifact_registry"], "claims": evidence["claims"],
        "plots": evidence["plots"], "exposure": evidence["exposure"], "access_control_status": evidence["access_control_status"],
        "omitted_evidence": list(report["omitted_evidence"]), "evidence_completeness": dict(report["evidence_completeness"]),
        "recorded_dimensions": report["dimensions"], "comparison_source_reverified": reverified,
        "review_evidence": {"gene_tables": [], "source_contexts": [], "plot_evidence": [], "method_context": None}}
    if reverified:
        from .rna_publication_comparison import assessment_review_evidence
        projection = assessment_review_evidence(report["subject"]["subject_ref"]["manifest_path"],
            report["subject"]["subject_ref"]["root_hash"], workspace=workspace,
            artifact_service=artifact_service, allow_evidence_read=True)
        _keys(projection, {"gene_tables", "source_contexts", "method_context", "omitted_evidence"})
        assessment["omitted_evidence"].extend(projection["omitted_evidence"])
        method_context = _method_context(projection["method_context"])
        assessment["review_evidence"]["method_context"] = method_context
        assessment["omitted_evidence"].extend(method_context["omitted_evidence"])
        if method_context["question"]["text"] is not None:
            sections["configuration"]["question"] = method_context["question"]["text"]
            sections["configuration"]["question_status"] = "captured_from_bound_assessment_intent"
        remaining_rows = 512
        for descriptor in projection["gene_tables"]:
            table = descriptor["table"]
            selected = table["rows"][:min(128, remaining_rows)]
            remaining_rows -= len(selected)
            assessment["review_evidence"]["gene_tables"].append({"table_role": descriptor["role"],
                "metric_ids": descriptor["metric_ids"], "table_id": table["table_id"], "alignment": table["alignment"],
                "family": {"family_id": table["family"]["family_id"], "complete": table["family"]["complete"],
                    "eligible_count": len(table["family"]["eligible_ids"]), "probability_semantics": table["family"]["probability_semantics"]},
                "rows": selected, "total_rows": len(table["rows"]), "omitted_rows": len(table["rows"])-len(selected)})
            if len(selected) != len(table["rows"]):
                assessment["omitted_evidence"].append({"kind": "tested_family", "reason": "per_feature_review_projection_limit", "count": len(table["rows"])-len(selected)})
        for context in projection["source_contexts"][:32]:
            projected = {"claim_id": context["claim_id"], "claim_text": context["claim_text"],
                "context_review": {key: context["context_review"][key] for key in ("checks", "conclusion", "limitations")},
                "excerpt": context["excerpt"]}
            if len(json.dumps(projected, ensure_ascii=False, allow_nan=False).encode()) > 16000:
                assessment["omitted_evidence"].append({"kind": "source_claim", "reason": "source_context_review_projection_limit", "count": 1})
            else:
                assessment["review_evidence"]["source_contexts"].append(projected)
        if len(projection["source_contexts"]) > 32:
            assessment["omitted_evidence"].append({"kind": "source_claim", "reason": "source_context_count_limit", "count": len(projection["source_contexts"])-32})
        _capture_reviewed_plot_evidence(assessment, evidence, artifact_service)
    if not assessment["review_evidence"]["gene_tables"]:
        assessment["omitted_evidence"].append({"kind": "tested_family", "reason": "reviewer_numeric_tables_not_captured", "count": 1})
    if not assessment["review_evidence"]["source_contexts"]:
        assessment["omitted_evidence"].append({"kind": "source_claim", "reason": "reviewer_source_context_not_captured", "count": 1})
    if not reverified:
        assessment["omitted_evidence"].append({"kind": "execution_receipt", "reason": "comparison_provenance_not_reverified_for_review", "count": 1})
    if not _method_context_available(assessment["review_evidence"]["method_context"]):
        assessment["omitted_evidence"].append({"kind": "method_context", "reason": "reviewer_method_context_not_captured", "count": 1})
    assessment["evidence_completeness"] = {"complete": not assessment["omitted_evidence"],
        "omitted_count": sum(item["count"] for item in assessment["omitted_evidence"])}
    return root, subject, sections, inputs, assessment


def _capture_reviewed_plot_evidence(assessment, evidence, artifact_service):
    from .figure_artifacts import load_publication_figure
    registry = {item["evidence_id"]: item for item in evidence["artifact_registry"]}
    for plot in evidence["plots"]:
        captured = False
        for key in plot["evidence_ids"]:
            item = registry[key]
            ref = item["artifact_ref"]
            if item["kind"] != "reviewed_plot" or "manifest_path" not in ref:
                continue
            try:
                _, inspection, _ = _load(ref["manifest_path"], ref["root_hash"], "pixel-review.json")
                figure, payload = load_publication_figure(inspection["figure_ref"]["manifest_path"],
                    inspection["figure_ref"]["root_hash"], artifact_service=artifact_service)
                if inspection["figure_id"] != plot["figure_id"] or inspection["image_sha256"] != figure["artifacts"]["png"]["sha256"]:
                    raise ValueError("Plot inspection does not bind its pixels")
            except (OSError, KeyError, ValueError):
                continue
            rows = payload["data"]["rows"]
            assessment["review_evidence"]["plot_evidence"].append({"figure_id": plot["figure_id"],
                "plot_kind": figure["plot_kind"], "axes": payload["data"]["axes"], "rows": rows[:128],
                "total_rows": len(rows), "omitted_rows": max(0, len(rows)-128), "inspection_notes": inspection["observations"],
                "inspection_assurance": "recorded_inspection_not_qualified_independent_review",
                "image_sha256": inspection["image_sha256"], "images_shown_to_model": False,
                "provenance_status": figure["provenance_status"], "comparison_binding_status": figure["comparison_binding_status"]})
            assessment["omitted_evidence"].append({"kind": "reviewed_plot", "reason": "images_not_shown_to_model", "count": 1})
            if len(rows) > 128:
                assessment["omitted_evidence"].append({"kind": "reviewed_plot", "reason": "plot_numeric_projection_limit", "count": len(rows)-128})
            captured = True
            break
        if not captured:
            assessment["omitted_evidence"].append({"kind": "reviewed_plot", "reason": "plot_data_and_inspection_notes_not_captured", "count": 1})


def prepare_packet(output, *, subject_kind, subject_path, expected_root, research, allow_evidence_read=False,
                   proposal_packet_manifest=None, expected_proposal_packet_root=None,
                   assessment_workspace=None, assessment_artifact_service=None):
    if allow_evidence_read is not True:
        raise PermissionError("Preparing a research packet requires evidence-reading authorization")
    if (proposal_packet_manifest is None) != (expected_proposal_packet_root is None):
        raise ValueError("Supply the proposal packet manifest and retained root together")
    if proposal_packet_manifest is not None and subject_kind not in {"episode", "workbench"}:
        raise ValueError("Proposal evidence can only be joined to an episode or workbench result")
    research = _research(research)
    _retained(expected_root)
    path = _path(subject_path)
    if _text(subject_kind, 30) not in {"proposal", "episode", "workbench", "trial", "comparison"}:
        raise ValueError("Choose a supported proposal, result, trial or comparison review subject")
    if (assessment_workspace is None) != (assessment_artifact_service is None):
        raise ValueError("Supply trusted assessment workspace and artifact service together")
    if assessment_workspace is not None and subject_kind not in {"trial", "comparison"}:
        raise ValueError("Trusted assessment context applies only to trial/comparison subjects")
    location = path.parent.parent if subject_kind in {"proposal", "trial", "comparison"} else path
    output = require_disjoint_output(_path(output), [location])
    if output.exists():
        raise ValueError("Use a fresh research packet directory")
    assessment = None
    if subject_kind in {"trial", "comparison"}:
        root, subject, sections, inputs, assessment = _assessment_subject(path, expected_root,
            workspace=assessment_workspace, artifact_service=assessment_artifact_service)
        if subject["kind"] != subject_kind:
            raise ValueError("Assessment report kind does not match the requested review subject")
    else:
        capture = {"proposal": _proposal_subject, "episode": _episode_subject, "workbench": _workbench_subject}[subject_kind]
        root, subject, sections, inputs = capture(path, expected_root)
    if proposal_packet_manifest is not None:
        inputs.extend(_join_proposal_evidence(subject, sections, proposal_packet_manifest, expected_proposal_packet_root))
    require_disjoint_output(output, [root, *inputs])
    subject["original_location"] = str(root)
    subject["read_only_paths"] = [str(root), *map(str, inputs)]
    record = {"format": "devbench.scientific-evidence-packet/2" if assessment is not None else "devbench.scientific-evidence-packet/1", "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "subject": subject, "sections": sections, "research": research,
        "source_entailment_verified": False, "source_retrieval_performed": False,
        "source_verification": "recorded_at_packet_creation_not_continuous_origin_verification",
        "review_scope": _SCOPE, **_AUTHORITY}
    if assessment is not None:
        record["assessment"] = assessment
        _bound_assessment_packet(record)
    return _seal(output, "packet.json", record, "analysis.research_packet", scope=_SCOPE)


def _bound_assessment_packet(record):
    """Keep the artifact bound while retaining explicit omission counters."""
    from devbench.study.contracts import canonical_json_bytes
    assessment = record["assessment"]
    evidence = assessment["review_evidence"]
    omitted = {}
    while len(canonical_json_bytes(record)) > 900000:
        if evidence["source_contexts"]:
            evidence["source_contexts"].pop()
            kind = "source_claim"
        else:
            populated = [table for table in evidence["gene_tables"] if table["rows"]]
            if populated:
                table = populated[-1]; table["rows"].pop(); table["omitted_rows"] += 1
                kind = "tested_family"
            else:
                plots = [plot for plot in evidence["plot_evidence"] if plot["rows"] or plot["inspection_notes"]]
                if not plots:
                    raise ValueError("Core assessment metadata exceeds packet budget; no review verdict is assessable")
                plot = plots[-1]
                if plot["rows"]:
                    plot["rows"].pop(); plot["omitted_rows"] += 1
                else:
                    plot["inspection_notes"].pop()
                kind = "reviewed_plot"
        if kind not in omitted:
            item = {"kind": kind, "reason": "aggregate_review_packet_byte_limit", "count": 0}
            assessment["omitted_evidence"].append(item); omitted[kind] = item
        omitted[kind]["count"] += 1
    assessment["evidence_completeness"] = {"complete": not assessment["omitted_evidence"],
        "omitted_count": sum(item["count"] for item in assessment["omitted_evidence"])}


def _packet(manifest, expected_root):
    root, record, _ = _load(_path(manifest), expected_root, "packet.json")
    if (record.get("format") not in {"devbench.scientific-evidence-packet/1", "devbench.scientific-evidence-packet/2"} or record.get("review_scope") != _SCOPE
            or record.get("source_entailment_verified") is not False or record.get("source_retrieval_performed") is not False
            or any(record.get(key) is not value for key, value in _AUTHORITY.items())):
        raise ValueError("Unsupported scientific packet authority or scope")
    _keys(record["sections"], _SECTIONS)
    _retained(record["subject"]["root_hash"])
    _research(record["research"])
    if record["format"] == "devbench.scientific-evidence-packet/2":
        _keys(record["assessment"], {"artifact_registry", "claims", "plots", "exposure", "access_control_status",
            "omitted_evidence", "evidence_completeness", "recorded_dimensions", "comparison_source_reverified", "review_evidence"})
        from .rna_publication_evidence import validate_exposure
        # This is a sealed recorded projection. Current source verification is
        # disclosed separately; it cannot be inferred from a local packet hash.
        if record["subject"]["kind"] not in {"trial", "comparison"}:
            raise ValueError("Unsupported assessment packet subject")
        validate_exposure(record["assessment"]["exposure"])
        evidence = record["assessment"]["review_evidence"]
        _keys(evidence, {"gene_tables", "source_contexts", "plot_evidence"} | ({"method_context"} if "method_context" in evidence else set()))
        _method_context(evidence.get("method_context"))
        if type(record["assessment"]["comparison_source_reverified"]) is not bool:
            raise ValueError("Assessment source verification must remain explicit")
    return root, record


def verify_packet(manifest, expected_root, *, allow_evidence_read=False):
    if allow_evidence_read is not True:
        raise PermissionError("Packet verification requires evidence-reading authorization")
    _, record = _packet(manifest, expected_root)
    return {"valid": True, "subject_root": record["subject"]["root_hash"], "origin_rechecked": False,
        "scientific_validity_established": False, "scope": "packet_integrity_not_source_entailment_or_scientific_validation"}


def _assessment(value, packet):
    _keys(value, {"disposition", "rationale", "findings", "questions", "limitations"})
    if _text(value["disposition"], 30) not in {"accept", "qualified_accept", "needs_information", "reject", "unable_to_assess"}:
        raise ValueError("Unknown scientific reviewer disposition")
    _text(value["rationale"])
    claims = {claim["claim_id"] for claim in packet["research"]["claims"]}
    version2 = packet.get("format") == "devbench.scientific-evidence-packet/2"
    if version2:
        claims.update(claim["claim_id"] for claim in packet["assessment"]["claims"])
        claims.update(context["claim_id"] for context in packet["assessment"]["review_evidence"]["source_contexts"])
        complete = (packet["assessment"]["evidence_completeness"]["complete"]
            and _method_context_available(_method_context(packet["assessment"]["review_evidence"].get("method_context"))))
        if value["disposition"] == "qualified_accept":
            raise ValueError("An advisory reviewer cannot qualify scientific review")
        if not complete and value["disposition"] in {"accept", "reject"}:
            raise ValueError("Confident verdict forbidden after incomplete evidence projection")
        if (packet["assessment"]["recorded_dimensions"]["methodology"]["state"] == "invalid"
                and value["disposition"] == "accept"):
            raise ValueError("Model commentary cannot waive deterministic methodological failure")
    for finding in _list(value["findings"], 128):
        _keys(finding, {"claim_id", "section", "status", "reason"} | ({"evidence_ids", "claim_scope"} if version2 else set()))
        if ((finding["claim_id"] is not None and _identifier(finding["claim_id"]) not in claims)
                or _text(finding["section"], 100) not in _SECTIONS
                or _text(finding["status"], 30) not in {"supported", "concern", "needs_information", "not_assessed"}):
            raise ValueError("Review findings must refer to known claims and packet sections")
        _text(finding["reason"])
        if version2:
            evidence_ids = {item["evidence_id"] for item in packet["assessment"]["artifact_registry"]}
            cited = _list(finding["evidence_ids"], 128)
            if (any(type(item) is not str for item in cited) or len(cited) != len(set(cited)) or not set(cited) <= evidence_ids
                    or finding["claim_scope"] not in {"descriptive", "association", "hypothesis"}
                    or (finding["status"] == "supported" and (not cited or not complete))):
                raise ValueError("Reviewer claims require actual cited evidence and complete permitted scope")
    for key in ("questions", "limitations"):
        for text in _list(value[key], 64):
            _text(text)
    return _json_copy(value)


def record_scientific_review(output, packet_manifest, *, expected_root, reviewer_label, assessment,
                             qualification_statement, independence_statement, allow_review=False, selection_origin=None):
    if allow_review is not True:
        raise PermissionError("Recording scientific review requires explicit authorization")
    _text(reviewer_label, 200)
    _text(qualification_statement)
    _text(independence_statement)
    root, packet = _packet(packet_manifest, expected_root)
    assessment = _assessment(assessment, packet)
    if selection_origin is not None:
        from .scientific_review_agent import verify_selection_origin
        verify_selection_origin(selection_origin, expected_root, assessment)
    protected = packet["subject"].get("read_only_paths")
    if not isinstance(protected, list) or not protected:
        raise ValueError("Packet lacks subject output-containment bindings; prepare a new packet")
    output = require_disjoint_output(_path(output), [root, *protected])
    version2 = packet.get("format") == "devbench.scientific-evidence-packet/2"
    record = {"format": "devbench.scientific-review/2" if version2 else "devbench.scientific-review/1", "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "packet_root": expected_root, "packet_sha256": digest(packet), "reviewer_label": reviewer_label,
        "assessment": assessment, "qualification_statement": qualification_statement,
        "qualification_status": "not_established", "independence_statement": independence_statement,
        "independence_verified": False, "review_scope": _SCOPE, **_AUTHORITY}
    if version2:
        record.update(commentary_status="advisory_unverified", deterministic_result_override_allowed=False)
    if selection_origin is not None:
        record["selection_origin"] = _json_copy(selection_origin)
    return _seal(output, "scientific-review.json", record, "analysis.scientific_review_recorded", scope=_SCOPE)


def verify_scientific_review(manifest, expected_root, *, packet_manifest, packet_root, allow_evidence_read=False):
    if allow_evidence_read is not True:
        raise PermissionError("Scientific review verification requires evidence-reading authorization")
    _, record, _ = _load(_path(manifest), expected_root, "scientific-review.json")
    _, packet = _packet(packet_manifest, packet_root)
    expected_format = "devbench.scientific-review/2" if packet.get("format") == "devbench.scientific-evidence-packet/2" else "devbench.scientific-review/1"
    if (record.get("format") != expected_format or record.get("packet_root") != packet_root
            or record.get("packet_sha256") != digest(packet) or record.get("review_scope") != _SCOPE
            or record.get("qualification_status") != "not_established" or record.get("independence_verified") is not False
            or any(record.get(key) is not value for key, value in _AUTHORITY.items())):
        raise ValueError("Scientific review does not bind this packet or has unsupported authority claims")
    if expected_format == "devbench.scientific-review/2" and (record.get("commentary_status") != "advisory_unverified"
            or record.get("deterministic_result_override_allowed") is not False):
        raise ValueError("Advisory review cannot become numeric authority")
    _assessment(record["assessment"], packet)
    return {"valid": True, "packet_root": packet_root, "scientific_validity_established": False,
            "scope": "recorded_reviewer_assertions_not_qualified_scientific_judgment"}


def evidence_cli(args):
    if args.allow_evidence_read is not True:
        raise PermissionError("Evidence requests require --allow-evidence-read")
    path = _path(args.evidence_request)
    output = require_disjoint_output(_path(args.output), [path])
    request = decode_json(_read(path, 1_000_000))
    if not isinstance(request, dict):
        raise ValueError("Use an evidence request object")
    values = dict(request)
    operation = values.pop("operation", None)
    if operation == "prepare_packet":
        optional = {"proposal_packet_manifest", "expected_proposal_packet_root"}
        _keys(values, {"subject_kind", "subject_path", "expected_root", "research"} | (optional if optional & values.keys() else set()))
        if args.allow_review_record:
            raise ValueError("Packet preparation does not record a reviewer decision")
        return prepare_packet(output, **values, allow_evidence_read=True)
    if operation == "record_scientific_review":
        _keys(values, {"packet_manifest", "expected_root", "reviewer_label", "assessment", "qualification_statement", "independence_statement"})
        return record_scientific_review(output, **values, allow_review=args.allow_review_record)
    raise ValueError("Unknown evidence request operation")
