"""Bounded substantive workflow reviews over exact, sealed scientific material.

The runtime adapter is coordinator code, never serialized authority. One review
request obtains both advisory and actual claim-level responses. Neither workflow
acceptance nor a verified execution DAG qualifies scientific judgment.
"""
from copy import deepcopy
import hashlib
import json
import uuid

from .checkpoints import digest
from .episode_agent import AgentLimits
from .episode_proposals import _load, _seal, _absolute, _bind_manifest_snapshot
from .scientific_judgment import (
    parse_claim_packet, EvaluationPolicy, evaluate_judgments, policy_sha256,
    validate_judgment,
)
from .scientific_review import (
    _AUTHORITY, _SCOPE, load_claim_judgment, load_claim_packet, prepare_claim_packet,
    verify_packet, verify_scientific_review,
)
from .scientific_review_agent import request_review
from devbench.study.evidence import _read, decode_json
from devharness_core.provenance import verify_execution


STAGES = ("candidate_suitability", "comparison_design", "results_conclusions")
_AUTHORITY_LIMITS = {**_AUTHORITY, "canonical_change_granted": False, "promotion_granted": False,
    "deterministic_result_override_allowed": False, "qualification_status": "not_established",
    "independence_verified": False}


def _verify_plan_sources(planner, plan):
    for source in plan.get("sources", []):
        path = planner.methods.artifact_path(source["artifact_id"])
        content = _read(path, 10_000_000)
        if hashlib.sha256(content).hexdigest() != source["content_sha256"]:
            raise ValueError("Retrieved scientific source bytes changed")
        if source.get("excerpt") and source["excerpt"] not in content.decode("utf-8", errors="replace"):
            raise ValueError("Scientific source excerpt is absent from retained bytes")


def stage_material(plan, stage):
    """Pure stage projection: later execution cannot rewrite an earlier review."""
    if stage not in STAGES:
        raise ValueError("Choose an explicit supported scientific review stage")
    sources = [{key: deepcopy(value) for key, value in source.items() if key != "path"}
        for source in plan.get("sources", [])]
    material = {"format": "devbench.workflow-scientific-review-material/1", "stage": stage,
        "session_id": plan["session_id"], "context": deepcopy(plan["context"]), "sources": sources,
        "evaluation_purpose": plan.get("evaluation_purpose"),
        "user_parameter_requirements": deepcopy(plan.get("user_parameter_requirements", [])),
        "scientific_candidates": deepcopy(plan.get("scientific_candidates"))}
    if "scientific_contract" in plan:
        from .rna_scientific_contract import scientific_contract_material
        material["scientific_contract"] = scientific_contract_material(plan,
            scientific_only=stage == "candidate_suitability")
        material["scientific_contract_sha256"] = plan.get("scientific_contract_sha256")
    if stage != "candidate_suitability":
        candidates = []
        for item in plan.get("candidates", []):
            # These fields are recomputed by _inspect_applicability during
            # recommendation. Bind the frozen definitions and implementation
            # identity; current eligibility remains a separate execution gate.
            candidate = {key: deepcopy(value) for key, value in item.items()
                if key not in {"applicability", "blockers", "prerequisite_inspections"}}
            if "assumptions" in candidate:
                candidate["assumptions"] = [{key: value for key, value in assumption.items()
                    if key not in {"status", "evidence"}} for assumption in candidate["assumptions"]]
            candidates.append(candidate)
        material.update(trial_designs=deepcopy(plan.get("trial_designs", [])),
            reviewed_candidates=candidates)
    if stage == "results_conclusions":
        material.update(results=deepcopy(plan.get("results", [])),
            recommendation=deepcopy(plan.get("recommendation")),
            preliminary_explanation=deepcopy(plan.get("preliminary_explanation")))
    if stage == "candidate_suitability":
        from .method_planner_contract import _reject_capabilities
        _reject_capabilities(material)
    from devbench.study.contracts import canonical_json_bytes
    if len(canonical_json_bytes(material)) > 96_000:
        raise ValueError("Scientific review material budget exhausted; scope a smaller explicit packet")
    return material


def review_material(planner, session, stage):
    plan = planner.get(session)
    _verify_plan_sources(planner, plan)
    return stage_material(plan, stage)


def _bind_claim_sources(claim, material):
    """A cited source must resolve this planning cycle's retained acquisition."""
    for evidence in claim.evidence:
        if evidence.kind != "source":
            continue
        matching = [source for source in material["sources"]
            if source.get("content_sha256") == evidence.sha256
            and evidence.source.url in {source.get("requested_url"), source.get("final_url")}]
        if not matching:
            raise ValueError("Claim citation has no exact retained workflow source acquisition")


def _verify_claim_packet_input(reference, claim):
    """Bind configured source bytes separately from the sealed packet identity."""
    if reference is None:
        return
    if (type(reference) is not dict or set(reference) != {"path", "sha256", "size_bytes"}
            or type(reference["size_bytes"]) is not int or not 0 < reference["size_bytes"] <= 1_000_000):
        raise ValueError("Claim packet input requires a bounded exact file reference")
    content = _read(_absolute(reference["path"]), 1_000_000)
    if (len(content) != reference["size_bytes"]
            or hashlib.sha256(content).hexdigest() != reference["sha256"]):
        raise ValueError("Configured claim packet input bytes changed")
    if parse_claim_packet(content) != claim:
        raise ValueError("Configured claim packet input differs from sealed review packet")


def _outcome(assessment, claim, dimensions, policy):
    if (dimensions["hard_failures"] or dimensions["objective_validity"] == "failed"
            or claim.execution in {"failed", "timed_out"}):
        return "blocked"
    if claim.intent in {"infer", "exclude"} and claim.objective_validity != "verified":
        return "blocked"
    if assessment["disposition"] in {"reject", "unable_to_assess", "qualified_accept"}:
        return "blocked"
    if dimensions["scientific_judgment"] == "demonstrable_error":
        return "blocked"
    if (assessment["disposition"] == "accept"
            and dimensions["scientific_judgment"] in {"supported", "defensible_alternative"}
            and not dimensions["failed_checks"]):
        return "accepted"
    scope = policy.get("descriptive_scope")
    if (policy.get("allow_descriptive") is True and isinstance(scope, str) and 0 < len(scope) <= 4000
            and claim.intent in {"retain", "diagnostic_flagging"}
            and assessment["disposition"] in {"accept", "needs_information"}
            and dimensions["scientific_judgment"] in {"insufficient_evidence", "not_assessed", "supported", "defensible_alternative"}):
        return "descriptive"
    return "blocked"


def _read_selection(receipt):
    manifest = _absolute(receipt["manifest_path"])
    if not verify_execution(manifest, receipt["root_hash"])["valid"]:
        raise ValueError("Scientific review execution evidence changed")
    document = decode_json(_read(manifest, 1_000_000))
    _bind_manifest_snapshot(document, receipt["root_hash"])
    content = _read(manifest.parent.parent / "selection.json", 1_000_000)
    expected = next(item for item in document["artifacts"] if item["name"] == "selection.json")
    if hashlib.sha256(content).hexdigest() != expected["sha256"]:
        raise ValueError("Scientific reviewer response changed")
    return decode_json(content)["decision"]


class WorkflowScientificReviewAdapter:
    """Trusted live capabilities stay here, outside checkpoint/policy JSON.

    ``claim_packet_builder(planner, session, stage, material)`` must supply the
    actual coordinator checks and exact evidence. It cannot substitute a pause,
    model self-report or generic acceptance for deterministic scientific facts.
    """
    def __init__(self, *, output_root, selector, claim_packet_builder, reviewer_label,
                 evaluation_policy, expected_policy_sha256, limits=None):
        self.output_root = _absolute(output_root)
        self.selector, self.claim_packet_builder = selector, claim_packet_builder
        self.reviewer_label = reviewer_label
        self.evaluation_policy = EvaluationPolicy.model_validate_json(evaluation_policy.model_dump_json())
        if policy_sha256(self.evaluation_policy) != expected_policy_sha256:
            raise ValueError("Frozen evaluation policy digest mismatch")
        if reviewer_label not in self.evaluation_policy.independent_reviewer_ids:
            raise ValueError("Review identity must bind the frozen panel")
        if not callable(claim_packet_builder) or not callable(getattr(selector, "select", None)):
            raise TypeError("Use trusted packet-builder and reviewer runtime adapters")
        self.expected_policy_sha256 = expected_policy_sha256
        self.limits = limits or AgentLimits(max_selections=1)

    def reconcile(self, planner, session, *, stage, policy, actor):
        """Reuse a completed receipt; an entered but unsealed request never reruns."""
        binding = {"session_id": session, "stage": stage,
            "material_sha256": digest(review_material(planner, session, stage)),
            "plan_sha256": digest(planner.get(session)), "policy_sha256": digest(policy)}
        if not self.output_root.exists():
            return None
        intents = list(self.output_root.glob("*/operation.json"))
        if len(intents) > 256:
            raise ValueError("Scientific review recovery index exceeds its bounded scan")
        matches = []
        for intent in intents:
            if decode_json(_read(intent, 128_000)) != binding:
                continue
            path = intent.parent / "review-evidence.json"
            if not path.is_file():
                raise ValueError("Scientific review dispatch is uncertain; reconcile before any new provider request")
            envelope = decode_json(_read(path, 1_000_000))
            record = envelope["record"]
            artifact = planner.methods.import_artifact(path, artifact_type="json", actor=actor)
            evidence = {"record_ref": {key: artifact[key] for key in ("artifact_id", "sha256", "artifact_type", "size_bytes")},
                **{key: record[key] for key in ("status", "material_sha256", "plan_sha256", "stage")}}
            verify_review(planner, session, stage, evidence, policy=policy)
            matches.append(evidence)
        if len(matches) > 1:
            raise ValueError("Multiple review receipts match this operation; explicit reconciliation is required")
        return matches[0] if matches else None

    def review(self, planner, session, *, stage, policy, actor, allow_model_execution=False):
        material = review_material(planner, session, stage)
        plan = planner.get(session)
        frozen_policy = deepcopy(policy)
        digest(frozen_policy)
        if type(frozen_policy) is not dict:
            raise ValueError("Use an explicit frozen review policy object")
        if ("evaluation_policy_sha256" in frozen_policy
                and frozen_policy["evaluation_policy_sha256"] != self.expected_policy_sha256):
            raise ValueError("Workflow and reviewer evaluation policies differ")
        retained = self.reconcile(planner, session, stage=stage, policy=frozen_policy, actor=actor)
        if retained is not None:
            return retained
        claim = self.claim_packet_builder(planner, session, stage, deepcopy(material))
        claim = parse_claim_packet(claim)
        input_reference = getattr(self.claim_packet_builder, "input_reference", None)
        if input_reference is not None and not callable(input_reference):
            raise TypeError("Claim packet input reference must be a trusted callable")
        claim_packet_input_ref = deepcopy(input_reference(stage)) if input_reference is not None else None
        _verify_claim_packet_input(claim_packet_input_ref, claim)
        if claim.input_root != digest(material):
            raise ValueError("Claim packet must bind the exact workflow review material")
        _bind_claim_sources(claim, material)
        if stage == "candidate_suitability":
            from .method_planner_contract import _reject_capabilities
            from .scientific_review_agent import claim_review_context
            _reject_capabilities(claim_review_context(claim))
        root = self.output_root / uuid.uuid4().hex
        root.mkdir(parents=True, mode=0o700)
        from .checkpoints import atomic_json
        atomic_json(root / "operation.json", {"session_id": session, "stage": stage,
            "material_sha256": digest(material), "plan_sha256": digest(plan), "policy_sha256": digest(frozen_policy)})
        claim_receipt = prepare_claim_packet(root / "claim-packet", claim, allow_evidence_read=True)
        # The existing advisory projection forwards this explicit evidence block
        # without broadening the planner's deliberately tool-less context.
        packet = {"format": "devbench.scientific-evidence-packet/1", "subject": {
            "kind": "workflow_" + stage, "root_hash": digest(material), "read_only_paths": [str(root / "material")]},
            "sections": {"configuration": {"question": claim.objective}, "admission": {}, "design": {},
                "provenance": {}, "runtime": {}, "numerical_results": {}, "diagnostics": {},
                "limitations": {"workflow_stage": stage, "reviewed_material": material,
                    "scope": "Advisory workflow gate; scientific qualification and independence remain unverified."},
                "execution": {"status": claim.execution}},
            "research": {"researcher_label": actor, "sources": [], "claims": [{"claim_id": claim.claim_id,
                "text": claim.claim_text, "basis": "inference", "source_ids": [], "subject_sections": ["limitations"],
                "applicability": "Exact workflow stage and attached verified claim evidence.",
                "limitations": "Source entailment and scientific suitability require actual review."}]},
            "source_entailment_verified": False, "source_retrieval_performed": False,
            "review_scope": _SCOPE, **_AUTHORITY}
        packet_receipt = _seal(root / "packet", "packet.json", packet, "analysis.workflow_review_packet")
        result = request_review(root / "request", packet_receipt["manifest_path"],
            expected_root=packet_receipt["root_hash"], reviewer_label=self.reviewer_label, selector=self.selector,
            limits=self.limits, allow_evidence_read=True, allow_review=True,
            allow_model_execution=allow_model_execution, model_data_scope=plan.get("data_classification"),
            claim_packet_manifest=claim_receipt["manifest_path"], claim_packet_root=claim_receipt["root_hash"],
            claim_reviewer_id=self.reviewer_label)
        if digest(planner.get(session)) != digest(plan) or review_material(planner, session, stage) != material:
            raise ValueError("Planner changed during substantive scientific review")
        _verify_claim_packet_input(claim_packet_input_ref, claim)
        record = {"format": "devbench.workflow-scientific-review/1", "stage": stage, "session_id": session,
            "material_sha256": digest(material), "plan_sha256": digest(plan), "policy": frozen_policy,
            "policy_sha256": digest(frozen_policy), "packet_receipt": packet_receipt,
            "claim_packet_receipt": claim_receipt, "request_result": result,
            "claim_packet_input_ref": claim_packet_input_ref,
            "evaluation_policy": self.evaluation_policy.model_dump(mode="json"),
            "evaluation_policy_sha256": self.expected_policy_sha256,
            "claim_judgment": None, "panel_evaluation": None, "status": "blocked", **_AUTHORITY_LIMITS}
        if result["status"] == "review_recorded" and result.get("claim_review_receipt"):
            judgment = load_claim_judgment(result["claim_review_receipt"]["manifest_path"],
                result["claim_review_receipt"]["root_hash"], packet_manifest=claim_receipt["manifest_path"],
                packet_root=claim_receipt["root_hash"], allow_evidence_read=True)
            decision = _read_selection(result["selection_receipt"])
            if judgment.model_dump(mode="json") != decision["claim_judgment"]:
                raise ValueError("Claim review differs from actual recorded provider response")
            record["claim_judgment"] = judgment.model_dump(mode="json")
            record["panel_evaluation"] = evaluate_judgments(claim, (judgment,), policy=self.evaluation_policy,
                expected_policy_sha256=self.expected_policy_sha256, primary_score=None)
            record["status"] = _outcome(decision, claim, validate_judgment(claim, judgment), frozen_policy)
        receipt = _seal(root / "record", "workflow-review.json", record, "analysis.workflow_scientific_review")
        envelope = {"record": record, "receipt": receipt}
        path = root / "review-evidence.json"
        atomic_json(path, envelope)
        artifact = planner.methods.import_artifact(path, artifact_type="json", actor=actor)
        return {"record_ref": {key: artifact[key] for key in ("artifact_id", "sha256", "artifact_type", "size_bytes")},
            **{key: record[key] for key in ("status", "material_sha256", "plan_sha256", "stage")}}


def review(planner, session, *, stage, reviewer, policy, actor, allow_model_execution=False):
    """Missing runtime configuration blocks; no serialized policy can grant it."""
    if not isinstance(reviewer, WorkflowScientificReviewAdapter):
        return {"status": "blocked", "stage": stage, "record_ref": None,
            "reason": "substantive_scientific_review_adapter_required",
            "material_sha256": digest(review_material(planner, session, stage)), "plan_sha256": digest(planner.get(session))}
    return reviewer.review(planner, session, stage=stage, policy=policy, actor=actor,
        allow_model_execution=allow_model_execution)


def verify_review(planner, session, stage, evidence, *, plan_snapshot=None, current_relevance=True, policy=None):
    """Recheck exact material, retained bytes, actual responses and both receipts."""
    ref = evidence.get("record_ref")
    if not ref or set(ref) != {"artifact_id", "sha256", "artifact_type", "size_bytes"}:
        raise ValueError("Substantive review requires a bounded immutable evidence reference")
    path = planner.methods.artifact_path(ref["artifact_id"])
    content = _read(path, 1_000_000)
    if hashlib.sha256(content).hexdigest() != ref["sha256"] or len(content) != ref["size_bytes"]:
        raise ValueError("Scientific review evidence artifact changed")
    envelope = decode_json(content)
    receipt, record = envelope["receipt"], envelope["record"]
    _, sealed, _ = _load(receipt["manifest_path"], receipt["root_hash"], "workflow-review.json")
    if sealed != record:
        raise ValueError("Scientific review differs from immutable record")
    expected_policy = record["policy"] if policy is None else policy
    if plan_snapshot is not None:
        _verify_plan_sources(planner, plan_snapshot)
        if record["material_sha256"] != digest(stage_material(plan_snapshot, stage)):
            raise ValueError("Scientific review material snapshot is stale")
    if (record["stage"] != stage or record["session_id"] != session
            or (current_relevance and record["material_sha256"] != digest(review_material(planner, session, stage)))
            or record["policy_sha256"] != digest(expected_policy) or record["policy"] != expected_policy):
        raise ValueError("Scientific review material or frozen policy is stale")
    if any(record.get(key) != value for key, value in _AUTHORITY_LIMITS.items()):
        raise ValueError("Scientific review carries unsupported authority")
    for key in ("status", "material_sha256", "plan_sha256", "stage"):
        if evidence.get(key) != record[key]:
            raise ValueError("Scientific review result binding differs")
    packet_ref, claim_ref = record["packet_receipt"], record["claim_packet_receipt"]
    verify_packet(packet_ref["manifest_path"], packet_ref["root_hash"], allow_evidence_read=True)
    claim = load_claim_packet(claim_ref["manifest_path"], claim_ref["root_hash"], allow_evidence_read=True)
    _verify_claim_packet_input(record.get("claim_packet_input_ref"), claim)
    if claim.input_root != record["material_sha256"]:
        raise ValueError("Scientific claim material binding differs")
    _, packet, _ = _load(packet_ref["manifest_path"], packet_ref["root_hash"], "packet.json")
    material = packet["sections"]["limitations"]["reviewed_material"]
    if digest(material) != record["material_sha256"]:
        raise ValueError("Actual reviewer material differs from frozen workflow material")
    _bind_claim_sources(claim, material)
    result = record["request_result"]
    for key in ("receipt", "selection_receipt"):
        run_receipt = result[key]
        if not verify_execution(_absolute(run_receipt["manifest_path"]), run_receipt["root_hash"])["valid"]:
            raise ValueError("Retained scientific reviewer execution failed verification")
    if result["status"] != "review_recorded" or not result.get("claim_review_receipt"):
        if record["status"] != "blocked":
            raise ValueError("An unavailable reviewer cannot produce a successful review")
        return record
    advisory_ref, judgment_ref = result["review_receipt"], result["claim_review_receipt"]
    verify_scientific_review(advisory_ref["manifest_path"], advisory_ref["root_hash"],
        packet_manifest=packet_ref["manifest_path"], packet_root=packet_ref["root_hash"], allow_evidence_read=True)
    judgment = load_claim_judgment(judgment_ref["manifest_path"], judgment_ref["root_hash"],
        packet_manifest=claim_ref["manifest_path"], packet_root=claim_ref["root_hash"], allow_evidence_read=True)
    decision = _read_selection(result["selection_receipt"])
    _, advisory, _ = _load(advisory_ref["manifest_path"], advisory_ref["root_hash"], "scientific-review.json")
    from .scientific_review_agent import verify_selection_origin
    verify_selection_origin(advisory["selection_origin"], packet_ref["root_hash"], advisory["assessment"])
    if judgment.model_dump(mode="json") != decision["claim_judgment"] or record["claim_judgment"] != decision["claim_judgment"]:
        raise ValueError("Claim review is not the actual retained response")
    panel = EvaluationPolicy.model_validate_json(json.dumps(record["evaluation_policy"], allow_nan=False))
    expected = evaluate_judgments(claim, (judgment,), policy=panel,
        expected_policy_sha256=record["evaluation_policy_sha256"], primary_score=None)
    if record["panel_evaluation"] != expected:
        raise ValueError("Frozen scientific panel evaluation changed")
    if record["status"] != _outcome(decision, claim, validate_judgment(claim, judgment), expected_policy):
        raise ValueError("Scientific review outcome differs from verified evidence")
    return record
