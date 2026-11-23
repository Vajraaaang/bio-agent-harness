"""Bounded substantive workflow reviews over exact, sealed scientific material.

The runtime adapter is coordinator code, never serialized authority. One review
request obtains both advisory and actual claim-level responses. Neither workflow
acceptance nor a verified execution DAG qualifies scientific judgment.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import json
import uuid

from .checkpoints import digest
from .episode_agent import AgentLimits
from .episode_proposals import _load, _seal, _absolute, _bind_manifest_snapshot
from .evidence_access import EvidenceBroker
from .scientific_judgment import (
    parse_claim_packet, EvaluationPolicy, evaluate_judgments, policy_sha256,
    validate_judgment,
)
from .scientific_review import (
    ActiveReviewPacket, _AUTHORITY, _SCOPE, load_claim_judgment, load_claim_packet, prepare_claim_packet,
    verify_packet, verify_scientific_review,
)
from .scientific_review_agent import request_review
from .scientific_review_holds import ReviewHoldLifecycle
from devbench.study.evidence import _read, decode_json
from devharness_core.provenance import verify_execution


STAGES = ("candidate_suitability", "comparison_design", "results_conclusions")
_AUTHORITY_LIMITS = {**_AUTHORITY, "canonical_change_granted": False, "promotion_granted": False,
    "deterministic_result_override_allowed": False, "qualification_status": "not_established",
    "independence_verified": False}


@dataclass(frozen=True)
class ActiveWorkflowReview:
    """Live host capabilities for a v3 packet; never reconstructed from model JSON."""

    packet: "ActiveReviewPacket"
    lifecycle: "ReviewHoldLifecycle"
    broker: "EvidenceBroker"
    context_id: str
    reveal_context_id: str
    controller_context_id: str
    query_handles: tuple = ()
    literature_handles: tuple = ()

    def __post_init__(self):
        if (not isinstance(self.packet, ActiveReviewPacket) or type(self.lifecycle) is not ReviewHoldLifecycle
                or type(self.broker) is not EvidenceBroker):
            raise TypeError("Use coordinator-owned active review capabilities")


def _verify_active_current(record, active, *, connection=None):
    if type(active) is not ActiveWorkflowReview:
        raise ValueError("Current active review requires its trusted lifecycle; historical verification is explicit")
    result = record["active_review"]
    if digest(active.packet.model_dump(mode="json")) != result["packet_sha256"]:
        raise ValueError("Active review packet is no longer current")
    state = active.lifecycle.state(connection=connection)
    cycle = state["cycles"].get(result["cycle_id"])
    if cycle is None or cycle["binding_sha256"] != active.packet.binding.sha256():
        raise ValueError("Active review lifecycle binding is stale")
    relevant = tuple(cycle["subject_entity_ids"])
    for identity in relevant:
        latest = active.lifecycle._latest_cycle(state, active.packet.binding, active.packet.touchpoint, identity)
        # A design advisory claim without a fit cannot confer primary admission.
        if latest is None and active.packet.touchpoint == "design" and state["entities"][identity]["kind"] != "fit":
            continue
        if latest is None or latest["cycle_id"] != cycle["cycle_id"]:
            raise ValueError("Active review is no longer the latest scoped assessment")
    if record["status"] == "accepted" and active.lifecycle.blocking_entities(active.packet.binding,
            touchpoint=active.packet.touchpoint, entity_ids=relevant, connection=connection):
        raise ValueError("Active review has a new unresolved dependent hold")
    literature = {handle.handle_id: handle for handle in active.literature_handles}
    for receipt in result.get("literature_receipts", []):
        if receipt["handle_id"] not in literature:
            raise ValueError("Current Literature review entitlement is unavailable")
        if receipt["status"] != "unavailable":
            literature[receipt["handle_id"]].service.replay(receipt["snapshot_id"], require_current=True)
    from .literature_adapter import literature_use_current
    for assessment in result.get("literature_assessments", []):
        if assessment["eligible"] and not literature_use_current(active.lifecycle,
                assessment["binding"]["claim_id"], active.packet.binding.sha256(), assessment_id=assessment["assessment_id"],
                connection=connection):
            raise ValueError("Active Literature claim assessment is no longer current")


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


def _public_review_material(material):
    """Disambiguate the typed output-port role from private message roles.

    The authoritative scientific material and its hash remain unchanged. The
    generic public-context guard still rejects every other private ``role``.
    """
    public = deepcopy(material)
    params = (public.get('recommendation') or {}).get('params')
    if isinstance(params, dict):
        params.pop('seed', None)
        for sub in params.values():
            if isinstance(sub, dict):
                sub.pop('seed', None)
    for result in public.get('results', []):
        if not isinstance(result, dict):
            continue
        for artifact in result.get('artifacts', []):
            if isinstance(artifact, dict) and 'role' in artifact:
                if 'artifact_role' in artifact:
                    raise ValueError('Scientific artifact role projection has ambiguous fields')
                artifact['artifact_role'] = artifact.pop('role')
    return public


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
    if assessment["disposition"] in {"reject", "unable_to_assess"}:
        return "blocked"
    if dimensions["scientific_judgment"] == "demonstrable_error":
        return "blocked"
    if (assessment["disposition"] == "accept"
            and dimensions["scientific_judgment"] in {"supported", "defensible_alternative"}
            and not dimensions["failed_checks"]):
        return "accepted"
    disposition = assessment["disposition"]
    conditional_judgment = (
        (disposition == "needs_information" and dimensions["scientific_judgment"] == "insufficient_evidence")
        or (policy.get("allow_qualified_scrna_prefit") is True
            and disposition == "qualified_accept"
            and dimensions["scientific_judgment"] in {"supported", "defensible_alternative"})
    )
    if (policy.get("allow_conditional_scrna_prefit") is True
            and claim.claim_id in {"scoped_candidate_suitability", "scoped_comparison_design"}
            and claim.intent == "diagnostic_flagging" and claim.execution == "not_run"
            and conditional_judgment
            and not dimensions["failed_checks"]
            and any(check.check == "contrast" and check.status == "verified"
                    and "verified_prefit" in check.evidence_ids for check in claim.objective_checks)):
        # A pre-fit advisory review can advance planning. The trial still
        # requires operator authority and the exact conditional method binding.
        return "descriptive"
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
                 evaluation_policy, expected_policy_sha256, limits=None, active_review_builder=None):
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
        if active_review_builder is not None and not callable(active_review_builder):
            raise TypeError("Active review builder must be a trusted callable")
        self.active_review_builder = active_review_builder
        self._review_workspaces = []

    @staticmethod
    def adaptive_material(planner, workflow, *, connection=None):
        """Return exact owner-resolved material without model/review bookkeeping."""
        plan = deepcopy(planner.get(workflow.lineage_id))
        for name in ("selection_history", "selection_in_progress", "model_campaign", "created_at", "updated_at"):
            plan.pop(name, None)
        with workflow._read_connection(connection) as db:
            state = workflow._state(db)
            material = {"plan": plan, "basis": state.basis, "retired": state.retired, "sweeps": state.sweeps,
                "steps": {identity: {"step": step.model_dump(mode="json"), "generation": state.generations[identity],
                    "outcome": state.outcome(identity)} for identity, step in sorted(state.nodes.items())}}
        return material

    @staticmethod
    def adaptive_material_sha256(planner, workflow, *, connection=None):
        """Bind scientific material without model/review/disclosure bookkeeping."""
        return digest(WorkflowScientificReviewAdapter.adaptive_material(planner, workflow, connection=connection))

    @staticmethod
    def require_adaptive_review_currentness(planner, workflow, result, active, *, review_binding=None, connection=None):
        """Require a current completed advisory review; never grant publication."""
        from .scientific_review_holds import ReviewBinding
        from devbench.engine.workflow_operations import OperationBlocked
        basis = result.get("controller_review_basis")
        if (type(basis) is not dict or basis.get("material_sha256") !=
                WorkflowScientificReviewAdapter.adaptive_material_sha256(planner, workflow, connection=connection)):
            raise OperationBlocked("current_adaptive_review_material_required")
        if (type(active) is not ActiveWorkflowReview or active.lifecycle.ledger.path != workflow.ledger.path
                or active.lifecycle.lineage_id != workflow.lineage_id):
            raise OperationBlocked("current_active_review_capability_required")
        # Exploratory completion requires a current advisory review. It does
        # not grant the separate T16 publication/qualified-claim admission.
        _verify_active_current({"active_review": result, "status": "blocked"}, active, connection=connection)
        cycle = active.lifecycle.state(connection=connection)["cycles"][result["cycle_id"]]
        if review_binding is not None:
            for identity in cycle["subject_entity_ids"]:
                binding = review_binding(identity)
                if binding is not None and ReviewBinding.model_validate(binding).sha256() != cycle["binding_sha256"]:
                    raise OperationBlocked("current_adaptive_review_binding_required")
        with workflow._read_connection(connection) as db:
            for identity in (active.context_id, active.reveal_context_id, active.controller_context_id):
                active.broker._context_policy(db, identity)
            for handle in active.query_handles:
                descriptor = handle.descriptor()
                source = active.broker._source(db, descriptor.source_id)
                if (source["content_sha256"] != descriptor.source_sha256
                        or digest(handle.result) != descriptor.source_sha256
                        or digest(handle.bindings) != descriptor.bindings_sha256):
                    raise OperationBlocked("current_review_query_source_required")
        return True

    def _selector_for_request(self, request_root, *, model_invoker=None):
        """Bind each live review to its own private run without moving a planner.

        Original private storage and previous actor workspaces stay denied. Any
        protected invocation gate is carried through the constructor's exact
        root validation; it is never dropped to make a review start.
        """
        from .episode_codex import CodexActionSelector
        original = self.selector
        if not isinstance(original, CodexActionSelector):
            return original
        if model_invoker is not None:
            from .episode_agent import CampaignSelectionInvoker
            if type(model_invoker) is not CampaignSelectionInvoker:
                raise TypeError("Common reviewer storage requires a trusted campaign runtime")
            _, ledger, _, _ = model_invoker._runtime()
            if (original.private_root != model_invoker.planner.methods.root
                    or not ledger.path.is_relative_to(original.private_root)):
                raise PermissionError("Campaign reviewer storage must match its exact common private root")
            private_root, denied = original.private_root, original.denied_roots
        else:
            private_root = request_root
            denied = tuple(dict.fromkeys((*original.denied_roots,
                *([original.private_root] if original.private_root is not None else []), self.output_root)))
        selected = CodexActionSelector(requested_model=original.requested_model,
            workspace_parent=original.workspace_parent, private_root=private_root,
            executable=original.executable, reasoning_effort=original.reasoning_effort,
            memory_mb=original.memory_mb, denied_roots=denied, max_context_bytes=original.max_context_bytes,
            protected_model_gate=original.protected_model_gate, trace_sidecar=original.trace_sidecar)
        selected.workspaces = list(dict.fromkeys((*original.workspaces, *self._review_workspaces)))
        return selected

    def reconcile(self, planner, session, *, stage, policy, actor, attempt=0):
        """Reuse a completed receipt; an entered but unsealed request never reruns."""
        if self.active_review_builder is not None:
            return self._active_review(planner, session, stage=stage, policy=policy, actor=actor,
                                       reconcile_only=True)
        binding = {"session_id": session, "stage": stage,
            "material_sha256": digest(review_material(planner, session, stage)),
            "plan_sha256": digest(planner.get(session)), "policy_sha256": digest(policy)}
        if attempt:
            binding["attempt"] = attempt
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

    def _active_review(self, planner, session, *, stage, policy, actor, allow_model_execution=False,
                       reconcile_only=False):
        from .scientific_review import prepare_active_packet
        from .scientific_review_agent import request_active_review, _review_sources
        from .episode_agent import selector_configuration
        from .episode_agent import ReviewAgentLimits
        from .checkpoints import atomic_json

        material, plan = review_material(planner, session, stage), planner.get(session)
        active = self.active_review_builder(planner, session, stage, deepcopy(material))
        if type(active) is not ActiveWorkflowReview:
            raise TypeError("Active workflow builder must return trusted bound review capabilities")
        if active.packet.touchpoint != ("publication" if stage == "results_conclusions" else "design"):
            raise ValueError("Active review touchpoint differs from the requested workflow stage")
        if active.packet.workflow_material_sha256 != digest(material):
            raise ValueError("Active review must bind this exact workflow material")
        from .scientific_judgment import verified_artifacts
        claim = parse_claim_packet(self.claim_packet_builder(planner, session, stage, deepcopy(material)))
        verified_artifacts(claim)
        if claim.input_root != digest(material):
            raise ValueError("Active review scientific checks differ from the exact workflow material")
        if (claim.execution in {"failed", "timed_out"} or claim.objective_validity == "failed"
                or claim.intent in {"infer", "exclude"} and claim.objective_validity != "verified"
                or any(check.status == "failed" for check in claim.objective_checks)):
            raise ValueError("Deterministic scientific refusal cannot be overridden by active review")
        if policy.get("evaluation_policy_sha256", self.expected_policy_sha256) != self.expected_policy_sha256:
            raise ValueError("Frozen evaluation policy digest mismatch")
        identity = {"session_id": session, "stage": stage, "material_sha256": digest(material),
            "policy_sha256": digest(policy), "packet_sha256": digest(active.packet.model_dump(mode="json")),
            "reviewer_id": self.reviewer_label, "selector_sha256": digest(selector_configuration(self.selector)),
            "source_sha256": digest(_review_sources()), "limits_sha256": digest(asdict(self.limits)),
            "context_id": active.context_id, "reveal_context_id": active.reveal_context_id,
            "lineage_id": active.lifecycle.lineage_id}
        root = self.output_root / ("active-" + digest(identity))
        evidence_path = root / "review-evidence.json"
        if root.exists():
            if not evidence_path.is_file():
                raise ValueError("Active review dispatch is uncertain; reconcile retained lifecycle before retry")
            record = decode_json(_read(evidence_path, 1_000_000))["record"]
            _verify_active_current(record, active)
        else:
            if reconcile_only:
                return None
            root.mkdir(mode=0o700, parents=True)
            atomic_json(root / "operation.json", identity)
            packet_receipt = prepare_active_packet(root / "packet", active.packet)
            limits = self.limits if isinstance(self.limits, ReviewAgentLimits) else ReviewAgentLimits(
                max_selections=32, timeout_seconds=self.limits.timeout_seconds,
                session_timeout_seconds=self.limits.session_timeout_seconds, memory_mb=self.limits.memory_mb,
                max_context_bytes=self.limits.max_context_bytes, max_total_context_bytes=self.limits.max_total_context_bytes)
            from .episode_agent import CampaignSelectionInvoker
            invoker = (CampaignSelectionInvoker(planner, session, limits=limits,
                allow_model_execution=allow_model_execution) if "model_campaign" in plan else None)
            selected = self._selector_for_request(root / "request", model_invoker=invoker)
            try:
                result = request_active_review(root / "request", active.packet, lifecycle=active.lifecycle,
                    cycle_id="review-" + digest(identity)[:32], reviewer_id=self.reviewer_label, selector=selected,
                    broker=active.broker, context_id=active.context_id, reveal_context_id=active.reveal_context_id,
                    controller_context_id=active.controller_context_id, query_handles=active.query_handles, limits=limits,
                    literature_handles=active.literature_handles,
                    allow_model_execution=allow_model_execution, model_data_scope=plan.get("data_classification"),
                    model_invoker=invoker)
            finally:
                if selected is not self.selector:
                    self._review_workspaces = list(dict.fromkeys((*self._review_workspaces, *selected.workspaces)))
            if review_material(planner, session, stage) != material or planner.get(session) != plan:
                raise ValueError("Workflow material changed during active review")
            record = {"format": "devbench.workflow-scientific-review/2", "stage": stage, "session_id": session,
                "material_sha256": digest(material), "plan_sha256": digest(plan), "policy": deepcopy(policy),
                "policy_sha256": digest(policy), "packet_receipt": packet_receipt, "active_review": result,
                "status": "accepted" if result["status"] == "completed" and not result["blocked_entity_ids"] else "blocked",
                **_AUTHORITY_LIMITS}
            receipt = _seal(root / "record", "workflow-review.json", record, "analysis.workflow_scientific_review")
            atomic_json(evidence_path, {"record": record, "receipt": receipt})
        artifact = planner.methods.import_artifact(evidence_path, artifact_type="json", actor=actor)
        evidence = {"record_ref": {key: artifact[key] for key in ("artifact_id", "sha256", "artifact_type", "size_bytes")},
            **{key: record[key] for key in ("status", "material_sha256", "plan_sha256", "stage")}}
        verify_review(planner, session, stage, evidence, policy=policy, active_review=active)
        return evidence

    def review(self, planner, session, *, stage, policy, actor, allow_model_execution=False, attempt=0):
        if self.active_review_builder is not None:
            return self._active_review(planner, session, stage=stage, policy=policy, actor=actor,
                                       allow_model_execution=allow_model_execution)
        material = review_material(planner, session, stage)
        plan = planner.get(session)
        frozen_policy = deepcopy(policy)
        digest(frozen_policy)
        if type(frozen_policy) is not dict:
            raise ValueError("Use an explicit frozen review policy object")
        if ("evaluation_policy_sha256" in frozen_policy
                and frozen_policy["evaluation_policy_sha256"] != self.expected_policy_sha256):
            raise ValueError("Workflow and reviewer evaluation policies differ")
        retained = self.reconcile(planner, session, stage=stage, policy=frozen_policy, actor=actor, attempt=attempt)
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
            "material_sha256": digest(material), "plan_sha256": digest(plan), "policy_sha256": digest(frozen_policy), **({"attempt": attempt} if attempt else {})})
        claim_receipt = prepare_claim_packet(root / "claim-packet", claim, allow_evidence_read=True)
        # The existing advisory projection forwards this explicit evidence block
        # without broadening the planner's deliberately tool-less context.
        packet = {"format": "devbench.scientific-evidence-packet/1", "subject": {
            "kind": "workflow_" + stage, "root_hash": digest(material), "read_only_paths": [str(root / "material")]},
            "sections": {"configuration": {"question": claim.objective}, "admission": {}, "design": {},
                "provenance": {}, "runtime": {}, "numerical_results": {}, "diagnostics": {},
                "limitations": {"workflow_stage": stage, "reviewed_material": _public_review_material(material),
                    'material_projection': 'artifact_role_alias/1',
                    "scope": "Advisory workflow gate; scientific qualification and independence remain unverified."},
                "execution": {"status": claim.execution}},
            "research": {"researcher_label": actor, "sources": [], "claims": [{"claim_id": claim.claim_id,
                "text": claim.claim_text, "basis": "inference", "source_ids": [], "subject_sections": ["limitations"],
                "applicability": "Exact workflow stage and attached verified claim evidence.",
                "limitations": "Source entailment and scientific suitability require actual review."}]},
            "source_entailment_verified": False, "source_retrieval_performed": False,
            "review_scope": _SCOPE, **_AUTHORITY}
        packet_receipt = _seal(root / "packet", "packet.json", packet, "analysis.workflow_review_packet")
        selected = self._selector_for_request(root / 'request')
        try:
            result = request_review(root / "request", packet_receipt["manifest_path"],
                expected_root=packet_receipt["root_hash"], reviewer_label=self.reviewer_label, selector=selected,
                limits=self.limits, allow_evidence_read=True, allow_review=True,
                allow_model_execution=allow_model_execution, model_data_scope=plan.get("data_classification"),
                claim_packet_manifest=claim_receipt["manifest_path"], claim_packet_root=claim_receipt["root_hash"],
                claim_reviewer_id=self.reviewer_label)
        finally:
            if selected is not self.selector:
                self._review_workspaces = list(dict.fromkeys((*self._review_workspaces, *selected.workspaces)))
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


def verify_review(planner, session, stage, evidence, *, plan_snapshot=None, current_relevance=True, policy=None,
                  active_review=None):
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
    if record.get("format") == "devbench.workflow-scientific-review/2":
        packet_ref, result = record["packet_receipt"], record["active_review"]
        verify_packet(packet_ref["manifest_path"], packet_ref["root_hash"], allow_evidence_read=True)
        _, packet, _ = _load(packet_ref["manifest_path"], packet_ref["root_hash"], "packet.json")
        from .scientific_review import ActiveReviewPacket
        typed = ActiveReviewPacket.model_validate_json(json.dumps(packet))
        if (typed.binding.sha256() != result["binding_sha256"] or digest(packet) != result["packet_sha256"]
                or typed.touchpoint != ("publication" if stage == "results_conclusions" else "design")):
            raise ValueError("Active review packet or touchpoint binding differs")
        retained = result["receipt"]
        manifest = _absolute(retained["manifest_path"])
        if not verify_execution(manifest, retained["root_hash"])["valid"]:
            raise ValueError("Active review execution evidence changed")
        document = decode_json(_read(manifest, 1_000_000))
        _bind_manifest_snapshot(document, retained["root_hash"])
        content = _read(manifest.parent.parent / "result.json", 1_000_000)
        expected = next(item for item in document["artifacts"] if item["name"] == "result.json")
        if hashlib.sha256(content).hexdigest() != expected["sha256"]:
            raise ValueError("Active review result bytes changed")
        actual = decode_json(content)
        if actual != {key: value for key, value in result.items() if key != "receipt"}:
            raise ValueError("Active review differs from retained execution evidence")
        status = "accepted" if result["status"] == "completed" and not result["blocked_entity_ids"] else "blocked"
        if record["status"] != status:
            raise ValueError("Active review outcome differs from its actual phases")
        if current_relevance:
            _verify_active_current(record, active_review)
        return record
    packet_ref, claim_ref = record["packet_receipt"], record["claim_packet_receipt"]
    verify_packet(packet_ref["manifest_path"], packet_ref["root_hash"], allow_evidence_read=True)
    claim = load_claim_packet(claim_ref["manifest_path"], claim_ref["root_hash"], allow_evidence_read=True)
    _verify_claim_packet_input(record.get("claim_packet_input_ref"), claim)
    if claim.input_root != record["material_sha256"]:
        raise ValueError("Scientific claim material binding differs")
    _, packet, _ = _load(packet_ref["manifest_path"], packet_ref["root_hash"], "packet.json")
    material = deepcopy(packet["sections"]["limitations"]["reviewed_material"])
    projection = packet['sections']['limitations'].get('material_projection')
    if projection is not None:
        if projection != 'artifact_role_alias/1':
            raise ValueError('Unknown scientific review material projection')
        for result_row in material.get('results', []):
            if not isinstance(result_row, dict):
                continue
            for artifact in result_row.get('artifacts', []):
                if isinstance(artifact, dict) and 'artifact_role' in artifact:
                    if 'role' in artifact:
                        raise ValueError('Scientific artifact role projection is ambiguous')
                    artifact['role'] = artifact.pop('artifact_role')
    if digest(material) != record["material_sha256"]:
        frozen_material = stage_material(plan_snapshot if plan_snapshot is not None else planner.get(session), stage)
        if (projection != 'artifact_role_alias/1' or digest(frozen_material) != record["material_sha256"]
                or _public_review_material(frozen_material) != packet["sections"]["limitations"]["reviewed_material"]):
            raise ValueError("Actual reviewer material differs from frozen workflow material")
        material = frozen_material
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
