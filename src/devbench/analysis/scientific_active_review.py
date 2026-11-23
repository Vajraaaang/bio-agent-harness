"""Metered evidence-first and narrative-reveal scientific review."""
from copy import deepcopy
from dataclasses import asdict
import json

from devharness_core.provenance import ExecutionDAG

from .checkpoints import digest
from .episode_agent import InvalidSelection, Selection, selector_configuration
from .episodes import _json_copy
from .method_planner_contract import _action_response_schema, evidence_query_arguments
from .model_authority import is_offline_selector, reservation_binding
from .scientific_review import _path


_MAX_INVALID_REVIEW_SELECTIONS = 2


def request_active_review(output, packet, *, lifecycle, cycle_id, reviewer_id, selector, broker,
                          context_id, reveal_context_id, controller_context_id, query_handles=(),
                          limits=None, allow_model_execution=False, model_data_scope=None, literature_handles=(),
                          model_invoker=None):
    """Run a metered evidence-first/reveal cycle with coordinator-owned capabilities.

    The model can query or object. Only the host lifecycle can admit holds, and
    resolving work remains subject to the existing workflow execution authority.
    """
    from . import scientific_review_agent as review_agent
    from .episode_agent import ReviewAgentLimits
    from .scientific_review import ActiveReviewPacket, active_review_context, prepare_active_packet
    from .scientific_review_holds import DiagnosticResolutionPredicate, ResolutionPredicate, ReviewHoldLifecycle
    from .evidence_access import EvidenceBroker
    from .episode_codex import CodexActionSelector
    from devbench.study.permissions import require_protected_storage
    from pydantic import TypeAdapter

    packet = ActiveReviewPacket.model_validate_json(ActiveReviewPacket.model_validate(packet).model_dump_json())
    limits = limits or ReviewAgentLimits(max_selections=32)
    if not isinstance(limits, ReviewAgentLimits) or type(lifecycle) is not ReviewHoldLifecycle or type(broker) is not EvidenceBroker:
        raise TypeError("Active review requires trusted lifecycle, broker and review limits")
    if broker.authority.ledger.path != lifecycle.authority.ledger.path:
        raise ValueError("Review and disclosure must share the scientific authority")
    configuration = selector_configuration(selector)
    if not is_offline_selector(configuration) and (not allow_model_execution or model_data_scope not in {"public", "synthetic"}):
        raise PermissionError("Live review requires explicit model authority and public/synthetic scope")
    from .episode_agent import CampaignSelectionInvoker
    if model_invoker is not None:
        if type(model_invoker) is not CampaignSelectionInvoker:
            raise TypeError("Review requires the common trusted campaign invocation wrapper")
        _, model_ledger, _, campaign = model_invoker._runtime()
        if model_ledger.path != lifecycle.ledger.path or campaign["session_id"] != lifecycle.lineage_id:
            raise ValueError("Reviewer campaign belongs to another scientific lineage")
    elif not is_offline_selector(configuration):
        raise PermissionError("Active review requires the shared cumulative model campaign")
    else:
        with lifecycle.ledger._database() as db:
            if lifecycle.ledger._model_schema(db) and db.execute(
                    "SELECT 1 FROM model_campaigns WHERE session_id=? LIMIT 1", (lifecycle.lineage_id,)).fetchone():
                raise PermissionError("Active review cannot bypass an existing cumulative model campaign")
    handles = {item.descriptor().handle_id: item for item in query_handles}
    from .literature_adapter import EntitledLiteratureQuery
    if any(type(item) is not EntitledLiteratureQuery for item in literature_handles):
        raise TypeError("Use coordinator-owned Literature capabilities")
    literature = {item.handle_id: item for item in literature_handles}
    if (len(literature) != len(literature_handles) or tuple(item.descriptor() for item in literature_handles) != packet.literature_handles
            or any(item.service.broker is not broker or item.context_id != context_id
                   or item.service.lineage_id != lifecycle.lineage_id for item in literature_handles)):
        raise ValueError("Literature entitlement differs from this exact initial review context")
    if len(handles) != len(query_handles) or tuple(item.descriptor() for item in query_handles) != packet.query_handles:
        raise ValueError("Review query capabilities differ from the frozen entitled handles")
    expected_phase = "design_initial" if packet.touchpoint == "design" else "publication_initial"
    with broker.authority.ledger._database() as db:
        db.execute("BEGIN")
        for identity, role, phase in ((context_id, "challenger", expected_phase),
                                     (reveal_context_id, "challenger", "narrative_reveal"),
                                     (controller_context_id, "controller", "analysis")):
            context, _ = broker._context_policy(db, identity)
            if (context["role"] != role or context["phase"] != phase or context["lineage_id"] != lifecycle.lineage_id
                    or identity == reveal_context_id and context_id not in context["parents"]):
                raise PermissionError("Review context has the wrong principal, phase or lineage")
    exposure = {name: broker.exposure(context_id, item.delivery.source_id) for name, item in handles.items()}
    exposure.update({name: broker.exposure(context_id, item.descriptor().source_id) for name, item in literature.items()})
    if (any(item.delivery.context_id != context_id or item.delivery.broker is not broker for item in handles.values())
            or exposure != packet.prior_exposure):
        raise ValueError("Review must bind current actual prior exposure")
    state = lifecycle.state()
    entity_ids = {claim.claim_id for claim in packet.claims}
    if any(identity not in state["entities"] or state["entities"][identity]["kind"] != "claim" for identity in entity_ids):
        raise ValueError("Review claims require registered dependency scope")
    while True:
        parents = {parent for identity in entity_ids for parent in state["entities"][identity]["dependencies"]}
        if parents <= entity_ids:
            break
        entity_ids |= parents
    obligations = state["obligations"].get(packet.binding.sha256(), {})
    required_checks = set(packet.validation_plan.checks) | {check for claim in packet.claims for check in claim.required_checks}
    if not required_checks <= obligations.keys():
        raise ValueError("Claim validation checks require registered current review obligations")
    for item in literature.values():
        for binding in item.bindings:
            obligation = obligations.get(binding.obligation_id)
            if obligation is None or obligation["entity_id"] != binding.claim_id or obligation["stage"] != "claim":
                raise ValueError("External assertions require their own frozen claim-only obligation")
            item.service.bind_claim(binding)
    static = packet.evidence.get("artifacts", {})
    if type(static) is not dict:
        raise ValueError("Inline review artifacts require exact named evidence")
    available_evidence = {item.delivery.source_id for item in handles.values()} | static.keys()
    cited_evidence = {identity for claim in packet.claims for identity in claim.evidence_ids}
    if not cited_evidence <= available_evidence:
        raise ValueError("Claim evidence is absent from this exact packet and its entitled handles")
    handle_scope = {}
    for name, item in handles.items():
        relevant = {claim.claim_id for claim in packet.claims if item.delivery.source_id in claim.evidence_ids}
        while True:
            parents = {parent for identity in relevant for parent in state["entities"][identity]["dependencies"]}
            if parents <= relevant:
                break
            relevant |= parents
        handle_scope[name] = tuple(sorted(relevant))
        lifecycle.register_evidence(item.delivery.source_id, item.descriptor().source_sha256,
                                    relevant_entity_ids=handle_scope[name])
    for identity, content in static.items():
        relevant = tuple(claim.claim_id for claim in packet.claims if identity in claim.evidence_ids)
        lifecycle.register_evidence(identity, digest(content), relevant_entity_ids=relevant)
    root = _path(output)
    require_protected_storage(root)
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    prepare_active_packet(root / "packet", packet)
    narrative_hash, plan_hash = digest(packet.controller_narrative), digest(packet.validation_plan.model_dump(mode="json"))
    classification = max((item["classification"] for item in exposure.values()),
        key=("structural", "diagnostic", "target_outcome", "unknown").index, default="unknown")
    lifecycle.open_cycle(cycle_id, packet.touchpoint, packet.binding, reviewer_id=reviewer_id,
        context_id=context_id, exposure=classification,
        exposure_receipt_ids=tuple(sorted({ref for item in exposure.values() for ref in item["reservation_ids"]})),
        entitled_handle_ids=tuple((*handles, *literature)), narrative_sha256=narrative_hash, validation_plan_sha256=plan_hash,
        entity_ids=tuple(sorted(entity_ids)), evidence_ids=tuple(sorted(cited_evidence)))
    purpose = "resolution" if any(response.get("rereview_cycle_id") == cycle_id
        for hold in lifecycle.state()["holds"].values() for response in hold["responses"]) else "review"
    sources = review_agent._review_sources()
    review_agent.atomic_json(root / "source-identity.json", sources)
    result = {"format": "devbench.active-review-run/1", "cycle_id": cycle_id, "status": "unavailable",
        "binding_sha256": packet.binding.sha256(), "packet_sha256": digest(packet.model_dump(mode="json")),
        "analysis_executed": False, "qualification_status": "not_established", "observed_tokens": 0,
        "unknown_token_invocations": 0, "query_receipts": [], "objections": [], "model_identities": [],
        "selector": configuration, "source_sha256": digest(sources), "limits": asdict(limits), "budget_purpose": purpose}
    if literature:
        result.update(literature_receipts=[], literature_assessments=[])
    audit = ExecutionDAG(root / "audit", actor_id="active-review-coordinator")
    deadline = review_agent.time.monotonic() + limits.timeout_seconds
    phase, pages, full_families, local_queries, context_bytes = "evidence_first", [], set(), 0, 0
    literature_pages, searched = [], set()
    required_families = {(name, family_id) for name, handle in handles.items() for family_id in handle.descriptor().family_ids}
    predicate = TypeAdapter(ResolutionPredicate | DiagnosticResolutionPredicate)
    max_context_bytes = min(limits.max_context_bytes, model_invoker.limits.max_context_bytes) if model_invoker else limits.max_context_bytes
    feedback, rejected, handle_offset = None, set(), 0

    def rejected_action_identity(action):
        # A corrected selection may repeat a decision after obtaining new evidence.
        semantic = ({key: value for key, value in action.items() if key != "rationale"}
                    if isinstance(action, dict) else action)
        return digest([phase, len(pages), len(literature_pages), handle_offset, semantic])

    def reject_selection(action, reason, *, details=None):
        fingerprint = rejected_action_identity(action)
        notes = result.setdefault("selection_feedback", [])
        note = {"turn": turn, "reason": reason, "response_sha256": digest(action)}
        if details:
            note["details"] = details
        notes.append(note)
        if fingerprint in rejected:
            raise TimeoutError("duplicate_rejected_review_action")
        rejected.add(fingerprint)
        if len(notes) >= _MAX_INVALID_REVIEW_SELECTIONS or turn >= limits.max_selections:
            raise TimeoutError("invalid_review_selection_budget_exhausted")
        return note

    try:
        for turn in range(1, limits.max_selections + 1):
            if review_agent._review_sources() != sources:
                raise ValueError("Active reviewer implementation changed during the cycle")
            context = active_review_context(packet, phase=phase)
            context["instructions"] += (
                " Return one response under the action field. Use query_evidence in family_summary mode for"
                " each required complete family, and filtered mode for bounded row inspection."
                " When query_handle_page offers a next offset, use page_query_handles to see the next page."
            )
            context.update(query_results=deepcopy(pages), review_cycle_id=cycle_id)
            if literature:
                context["literature_results"] = deepcopy(literature_pages)
            if feedback is not None:
                context["selection_feedback"] = feedback
            state = lifecycle.state()
            context["review_entities"] = [state["entities"][identity] for identity in sorted(entity_ids)]
            context["open_holds"] = [{**{key: hold[key] for key in ("hold_id", "binding_sha256", "objection", "scope")},
                "predicate_sha256": predicate.validate_python(hold["objection"]["predicate"]).sha256()}
                for hold in state["holds"].values() if hold["status"] == "open" and entity_ids.intersection(hold["scope"])]
            context["obligations"] = [{**item, "predicate_sha256": predicate.validate_python(item["predicate"]).sha256()
                if item["predicate"] is not None else None}
                for item in state["obligations"].get(packet.binding.sha256(), {}).values()
                if phase == "narrative_reveal" or item["stage"] == "pre_fit"]
            context = review_agent._page_review_query_handles(context, handle_offset,
                min(max_context_bytes, limits.max_total_context_bytes - context_bytes))
            raw_size = review_agent._context_size(context)
            context_bytes += raw_size
            remaining = deadline - review_agent.time.monotonic()
            if remaining <= 0 or raw_size > max_context_bytes or context_bytes > limits.max_total_context_bytes:
                raise TimeoutError("Review time or context grant exhausted")
            lifecycle.record_usage(cycle_id, invocations=1, turns=1, purpose=purpose)
            token_reservation = lifecycle.reserve_invocation(cycle_id, maximum_tokens=limits.max_tokens_per_invocation, purpose=purpose)
            source_id = f"review-input-{cycle_id}-{turn}"
            # The source binds the exact turn and inherits full outcome provenance.
            broker.register_source(source_id, content_sha256=digest(context), roots=(packet.binding.inputs_sha256,),
                classification=classification, parents=tuple(item.delivery.source_id for item in handles.values()),
                tags=("controller_narrative", "validation_plan") if phase == "narrative_reveal" else ())
            recipient = context_id if phase == "evidence_first" else reveal_context_id
            run = root / f"turn-{turn}"
            run.mkdir(mode=0o700)
            review_agent.atomic_json(run / "context.json", context)
            action_schema = review_agent._active_review_action_schema(context)
            response_schema = _action_response_schema(action_schema, title="ActiveScientificReviewAction")
            review_agent.atomic_json(run / "answer_schema.json", response_schema)
            attempt = {"request_sha256": result["packet_sha256"], "sequence": turn, "context": context,
                       "attempt_path": str(run)}
            review_agent.atomic_json(run / "selection-reservation.json", {"sequence": turn, "maximum_provider_sessions": 2,
                "model_execution_authorized": allow_model_execution is True,
                **reservation_binding(configuration, attempt)})
            authority = review_agent.read_model_authority(run / "selection-reservation.json", configuration=configuration,
                attempt=attempt, maximum_authority=allow_model_execution)
            selected = run / "selector"
            selected.mkdir(mode=0o700)
            if isinstance(selector, CodexActionSelector) and model_invoker is None:
                if selector.private_root is not None and selector.private_root != root:
                    raise ValueError("Active reviewer must have its own fresh private run")
                selector.private_root = root
            try:
                choice = broker.delivery(recipient, source_id).transmit(context, lambda disclosed:
                    model_invoker.invoke(selector, disclosed, selected, role="challenger", purpose="scientific_review",
                        node="review-" + cycle_id, attempt=turn,
                        timeout_seconds=min(remaining, limits.session_timeout_seconds), token_limit=limits.max_tokens_per_invocation,
                        memory_mb=limits.memory_mb, max_context_bytes=max_context_bytes)
                    if model_invoker is not None else
                    selector.select(disclosed, selected, timeout_seconds=min(remaining, limits.session_timeout_seconds),
                        memory_mb=limits.memory_mb, allow_model_execution=authority))
                if not isinstance(choice, Selection):
                    raise InvalidSelection("Review selector must return a Selection")
            except InvalidSelection:
                lifecycle.settle_invocation(cycle_id, token_reservation)
                if model_invoker is not None:
                    _, _, _, current_campaign = model_invoker._runtime()
                    if current_campaign["unresolved_count"] or current_campaign["qualification"] == "violated":
                        from devbench.engine.workflow_operations import OperationBlocked
                        raise OperationBlocked("current_campaign_reconciliation_required") from None
                feedback = reject_selection(None, "invalid_structured_review_response")
                continue
            tokens = choice.evidence.get("tokens")
            lifecycle.settle_invocation(cycle_id, token_reservation,
                tokens=tokens if type(tokens) is int and tokens >= 0 else None)
            if type(tokens) is int and tokens >= 0:
                result["observed_tokens"] += tokens
                if tokens > limits.max_tokens_per_invocation:
                    raise ValueError("Provider exceeded reserved reviewer token grant")
            else:
                result["unknown_token_invocations"] += 1
            result["model_identities"].append({key: choice.evidence.get(key) for key in
                ("actual_model", "model_sessions_attempted", "backend", "model_identity_verified",
                 "role_identity", "model_reservation_ref", "campaign_id", "protocol_scope")})
            if review_agent._review_sources() != sources:
                raise ValueError("Active reviewer implementation changed during the invocation")
            if review_agent.time.monotonic() >= deadline:
                raise TimeoutError("Review deadline expired during the invocation")
            try:
                raw_action = _json_copy(choice.action)
            except (ValueError, TypeError, RecursionError):
                feedback = reject_selection(None, "invalid_structured_review_response")
                continue
            review_agent.atomic_json(run / "selection.json", {"decision": raw_action, "evidence": choice.evidence})
            try:
                action = review_agent._validate_active_review_action(raw_action, action_schema, response_schema, context)
            except InvalidSelection:
                # Unsupported kinds have no entitlement in this turn.
                raise
            except ValueError:
                feedback = reject_selection(raw_action, "invalid_structured_review_response")
                continue
            if rejected_action_identity(action) in rejected:
                raise TimeoutError("duplicate_rejected_review_action")
            feedback = None
            if action["kind"] == "page_query_handles":
                handle_offset = context["query_handle_page"]["next_offset"] if action["direction"] == "next" else 0
                continue
            if action["kind"] == "query_literature":
                if local_queries >= limits.max_queries:
                    raise TimeoutError("Review query grant exhausted")
                lifecycle.record_usage(cycle_id, queries=1, purpose=purpose)
                local_queries += 1
                handle = literature[action["handle_id"]]
                if phase == "evidence_first":
                    from .literature_adapter import LiteratureRequest
                    requested = LiteratureRequest.model_validate_json(json.dumps(action["request"]))
                    preferred_queries = {" ".join(item[key].casefold().split()) for item in packet.controller_literature
                        for key in ("query", "preferred_search") if isinstance(item.get(key), str)}
                    preferred_ids = {item[key].casefold() for item in packet.controller_literature
                        for key in ("pmid", "pmcid", "doi", "identifier") if isinstance(item.get(key), str)}
                    found = {identity.casefold() for item in literature_pages
                        if item["handle_id"] == action["handle_id"] and item["claim_id"] == action["claim_id"]
                        and item["page"]["status"] == "available" and item["page"]["search"] is not None
                        and item["page"]["search"]["provider"] == requested.provider
                        for identity in item["page"]["search"]["identifiers"]}
                    query_text = " ".join((requested.query or "").casefold().split())
                    if (query_text and query_text in preferred_queries
                            or requested.identifier is not None and requested.identifier.casefold() in preferred_ids - found):
                        raise InvalidSelection("Controller-only Literature sources cannot seed independent counter-search before reveal")
                page = handle.query(action["request"], claim_id=action["claim_id"], context_id=recipient,
                                    request_id="review-" + digest([cycle_id, turn])[:40])
                receipt = page["query_receipt"]
                evidence_id = "literature-" + receipt["disclosure_reservation_id"]
                lifecycle.record_query_evidence(cycle_id, evidence_id, digest(page), relevant_entity_ids=(action["claim_id"],),
                    source_handle_id=action["handle_id"], content_identity_sha256=receipt["content_identity_sha256"])
                result["literature_receipts"].append({"handle_id": action["handle_id"], "claim_id": action["claim_id"],
                    "status": page["status"], **receipt})
                literature_pages.append({"handle_id": action["handle_id"], "claim_id": action["claim_id"],
                                         "evidence_id": evidence_id, "page": page})
                if phase == "evidence_first" and action["request"]["operation"] == "search" and page["status"] == "available":
                    searched.add((action["handle_id"], action["claim_id"]))
                review_agent.atomic_json(run / "literature.json", page)
                continue
            if action["kind"] == "query_evidence":
                if local_queries >= limits.max_queries:
                    raise TimeoutError("Review query grant exhausted")
                lifecycle.record_usage(cycle_id, queries=1, purpose=purpose)
                local_queries += 1
                handle, query = handles[action["handle_id"]], action["query"]
                page = handle.query(evidence_query_arguments(query))
                receipt = page["query_receipt"]
                evidence_id = "query-" + receipt["disclosure_reservation_id"]
                lifecycle.record_query_evidence(cycle_id, evidence_id, digest(page),
                    relevant_entity_ids=handle_scope[action["handle_id"]], source_handle_id=action["handle_id"],
                    content_identity_sha256=digest({"values": {key: value for key, value in page.items() if key != "query_receipt"},
                        "query": {key: receipt[key] for key in ("source_sha256", "query_sha256", "offset", "total_returned",
                            "status", "complete", "statistics_included")}}))
                result["query_receipts"].append(receipt)
                pages.append({"handle_id": action["handle_id"], "evidence_id": evidence_id, "page": page})
                if (receipt["complete"] and receipt["status"] == "available" and query.get("mode") == "family_summary"
                        and not set(query) & {"filters", "gene_ids", "cursor", "offset"}):
                    full_families.add((action["handle_id"], query.get("target_id")))
                review_agent.atomic_json(run / "query.json", page)
                continue
            if action["outcome"] == "completed" and packet.touchpoint == "publication":
                missing = sorted(required_families - full_families)
                if missing:
                    visible = {item["handle_id"] for item in context["query_handles"]}
                    feedback = reject_selection(action, "complete_family_query_required", details={
                        "remaining": len(missing), "remaining_sha256": digest(missing),
                        "visible_examples": [{"handle_id": name, "target_id": family_id}
                                             for name, family_id in missing if name in visible][:8]})
                    continue
            if any(not set(objection["affected_entity_ids"]) <= entity_ids for objection in action["objections"]):
                feedback = reject_selection(action, "objection_outside_review_scope")
                continue
            clarification = None
            for objection in action["objections"]:
                admitted = lifecycle.admit_objection(cycle_id, objection)
                result["objections"].append(admitted)
                if admitted["status"] == "clarification_required":
                    clarification = list(admitted["reasons"])
                    break
            if clarification is not None:
                feedback = reject_selection(action, "objection_requires_clarification", details=clarification)
                continue
            if phase == "evidence_first" and action["outcome"] == "completed":
                required_searches = {(name, item.claim_id) for name, handle in literature.items() for item in handle.bindings}
                if not required_searches <= searched:
                    raise InvalidSelection("Independent counter-search required before preferred sources or narrative reveal")
                lifecycle.record_initial_assessment(cycle_id, assessment_sha256=digest(action),
                    covered_obligation_ids=tuple(action["covered_obligation_ids"]))
                lifecycle.reveal_narrative(cycle_id, narrative_sha256=narrative_hash, validation_plan_sha256=plan_hash)
                phase = "narrative_reveal"
                continue
            approved_external = set()
            assessed_claims = set()
            for support in action.get("literature_assessments", []):
                if support["claim_id"] in assessed_claims:
                    raise InvalidSelection("Duplicate Literature assessment for the same claim")
                assessed_claims.add(support["claim_id"])
                candidates = [handle for handle in literature.values() if any(item.claim_id == support["claim_id"] for item in handle.bindings)]
                if len(candidates) != 1 or support["reviewer_id"] != reviewer_id:
                    raise InvalidSelection("Literature assessment lacks its exact reviewer/claim entitlement")
                claim = next(item for item in packet.claims if item.claim_id == support["claim_id"])
                assessed = candidates[0].assess(support, claim=claim, context_id=reveal_context_id)
                result["literature_assessments"].append(assessed)
                if assessed["eligible"]:
                    approved_external.add(support["claim_id"])
            external_obligations = {item.obligation_id: item.claim_id for handle in literature.values() for item in handle.bindings}
            rejected_external = {external_obligations[item["obligation_id"]]
                for item in action.get("resolution_assessments", []) if not item["satisfied"]
                and item.get("obligation_id") in external_obligations}
            hold_state = lifecycle.state()
            held_external = {identity for hold in hold_state["holds"].values()
                if hold["status"] == "open" for identity in hold["scope"]}
            from .scientific_review_holds import ResolutionAssessment
            for assessment in action.get("resolution_assessments", []):
                if (not assessment["satisfied"]
                        and hold_state["holds"].get(assessment.get("hold_id"), {}).get("status") == "resolved"):
                    decision = ResolutionAssessment.model_validate_json(json.dumps(assessment))
                    held = lifecycle._negative_hold(hold_state, lifecycle._cycle(hold_state, cycle_id), decision)
                    held_external.update(held["scope"])
            if action["outcome"] == "completed" and set(external_obligations.values()) - approved_external - rejected_external - held_external:
                raise InvalidSelection("Completed external claims require current applicable passage assessments or explicit rejection")
            if any(item["satisfied"] and item.get("obligation_id") in external_obligations
                   and external_obligations[item["obligation_id"]] not in approved_external for item in action.get("resolution_assessments", [])):
                raise InvalidSelection("External claim resolution requires current applicable passage assessment")
            with lifecycle.authority._execution_tx(lifecycle.lineage_id) as db:
                from .literature_adapter import literature_use_current
                from devbench.engine.workflow_operations import OperationBlocked
                for assessed in result.get("literature_assessments", []):
                    if assessed["eligible"] and not literature_use_current(lifecycle, assessed["binding"]["claim_id"],
                            packet.binding.sha256(), connection=db, assessment_id=assessed["assessment_id"]):
                        raise OperationBlocked("current_literature_claim_assessment_required")
                lifecycle.complete_cycle(cycle_id, assessment_sha256=digest(action),
                    covered_obligation_ids=tuple(action["covered_obligation_ids"]), outcome=action["outcome"],
                    assessment_content=action, connection=db)
                for assessment in action.get("resolution_assessments", []):
                    if not assessment["satisfied"]:
                        continue
                    if assessment.get("hold_id") is not None:
                        lifecycle.resolve_scientific(assessment["hold_id"], cycle_id=cycle_id,
                            evidence_ids=tuple(assessment["evidence_ids"]), assessment_sha256=digest(action), connection=db)
                    else:
                        lifecycle.assess_obligation(assessment["obligation_id"], packet.binding, cycle_id=cycle_id,
                            evidence_ids=tuple(assessment["evidence_ids"]), assessment_sha256=digest(action), connection=db)
            result["status"] = action["outcome"]
            break
        else:
            raise TimeoutError("Review invocation grant exhausted")
    except Exception as error:
        from devbench.engine.workflow_operations import OperationBlocked
        result.update(status="unavailable" if isinstance(error, (TimeoutError, OperationBlocked)) else "failed",
                      reason=type(error).__name__, detail=str(error)[:1000])
        if lifecycle.state()["cycles"][cycle_id]["phase"] not in {"completed", "failed", "unavailable"}:
            lifecycle.complete_cycle(cycle_id, assessment_sha256=digest({"failure": result["detail"]}),
                                     outcome="unavailable" if result["status"] == "unavailable" else "failed")
    result["blocked_entity_ids"] = list(lifecycle.blocking_entities(packet.binding,
        touchpoint=packet.touchpoint, entity_ids=tuple(sorted(entity_ids))))
    current = lifecycle.state()
    reservations = [item for item in current["reservations"].values() if item["cycle_id"] == cycle_id]
    result["unknown_token_invocations"] = sum(item["status"] in {"reserved", "unknown"} for item in reservations)
    result["observed_tokens_complete"] = result["unknown_token_invocations"] == 0
    result["observed_tokens"] = sum(item.get("observed_tokens") or 0 for item in reservations)
    result["accounting"] = current["accounting"]
    review_agent.atomic_json(root / "result.json", result)
    for child in sorted(root.iterdir()):
        if child.name != "audit":
            audit.add_artifact(child, max_bytes=32_000_000, max_entries=5000)
    audit.append("analysis.active_scientific_review", {"cycle_id": cycle_id, "status": result["status"]})
    result["receipt"] = audit.finalize(analysis_fingerprint=result["packet_sha256"])
    source_id = "review-return-" + cycle_id
    broker.register_source(source_id, content_sha256=digest(result), roots=(packet.binding.inputs_sha256,),
        classification=classification, parents=tuple(item.delivery.source_id for item in handles.values()))
    return broker.delivery(controller_context_id, source_id).release(result)
