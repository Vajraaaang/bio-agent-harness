"""Single-selection dispatch and evidence capture for historical review packets."""
from dataclasses import asdict
import json

from devharness_core.provenance import ExecutionDAG

from .checkpoints import digest
from .episode_agent import AgentLimits, InvalidSelection, Selection, selector_configuration
from .episode_proposals import require_disjoint_output
from .episodes import _json_copy, _public_only
from .model_authority import is_offline_selector, reservation_binding
from .scientific_review import _assessment, _packet, _path, _text


def request_review(output, packet_manifest, *, expected_root, reviewer_label, selector, limits=None,
                   allow_evidence_read=False, allow_review=False, allow_model_execution=False, model_data_scope=None,
                   claim_packet_manifest=None, claim_packet_root=None, claim_reviewer_id=None,
                   evidence_delivery=None, return_delivery=None):
    from . import scientific_review_agent as review_agent

    if allow_evidence_read is not True or allow_review is not True:
        raise PermissionError("Reviewer requests need evidence-reading and review-record authorization")
    from .evidence_access import EvidenceDelivery
    if any(value is not None and type(value) is not EvidenceDelivery for value in (evidence_delivery, return_delivery)):
        raise ValueError("Reviewer deliveries must be coordinator-owned")
    from .episode_codex import CodexActionSelector
    live = isinstance(selector, CodexActionSelector)
    configuration = selector_configuration(selector)
    if live and (allow_model_execution is not True or model_data_scope not in {"synthetic", "public"}):
        raise PermissionError("Live scientific review needs separate model authorization and public/synthetic disclosure scope")
    if (not is_offline_selector(configuration) and allow_model_execution is True
            and model_data_scope not in {"synthetic", "public"}):
        raise PermissionError("External model review requires public/synthetic disclosure scope")
    _text(reviewer_label, 200)
    policy = limits or AgentLimits()
    if not isinstance(policy, AgentLimits):
        raise ValueError("Use the existing AgentLimits")
    packet_dir, packet = _packet(packet_manifest, expected_root)
    if packet.get("format") == "devbench.scientific-evidence-packet/3":
        raise ValueError("Active review packets require the coordinator-owned active review lifecycle")
    claim_packet = None
    companions = (claim_packet_manifest, claim_packet_root, claim_reviewer_id)
    if any(value is not None for value in companions):
        if any(value is None for value in companions):
            raise ValueError("Supply claim packet manifest, retained root and reviewer identity together")
        from .scientific_review import load_claim_packet
        claim_packet = load_claim_packet(claim_packet_manifest, claim_packet_root, allow_evidence_read=True)
        if len(claim_reviewer_id) > 128 or not claim_reviewer_id:
            raise ValueError("Use a bounded claim reviewer identity")
    root = require_disjoint_output(_path(output), [packet_dir, *packet["subject"].get("read_only_paths", [])])
    if claim_packet is not None:
        require_disjoint_output(root, [_path(claim_packet_manifest).parent.parent,
            *[item.path for item in claim_packet.evidence]])
    from devbench.study.permissions import require_protected_storage
    require_protected_storage(root)
    if live:
        if selector.private_root is not None and selector.private_root != root:
            raise ValueError("Reviewer selector private storage must match its fresh run")
        selector.private_root = root
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    started = review_agent.time.monotonic()
    deadline = started + policy.timeout_seconds
    sources, packet_hash = review_agent._review_sources(), digest(packet)
    request = {"format": "devbench.scientific-review-request/1", "packet_root": expected_root, "packet_sha256": packet_hash,
        "reviewer_label": reviewer_label, "agent_limits": asdict(policy), "effective_max_selections": 1,
        "model_data_scope": model_data_scope, "selector": configuration, "source_sha256": digest(sources)}
    if claim_packet is not None:
        request.update(claim_packet_root=claim_packet_root, claim_packet_sha256=review_agent.claim_review_context(claim_packet)["packet_sha256"],
            claim_reviewer_id=claim_reviewer_id)
    audit = ExecutionDAG(root / "audit", actor_id="scientific-review-selector-coordinator")
    result = {"format": "devbench.scientific-review-run/1", "status": "infrastructure_failure", "review_receipt": None,
        "analysis_executed": False, "qualification_status": "not_established", "model_sessions_attempted": 0,
        "tokens": None, "cost_usd": None, "actual_model": None, "selector_entered": False}
    context = decision = None
    def remaining():
        value = deadline-review_agent.time.monotonic()
        if value <= 0:
            raise TimeoutError("scientific_review_deadline_exhausted")
        return value
    def unchanged():
        remaining()
        if digest(_packet(packet_manifest, expected_root)[1]) != packet_hash or review_agent._review_sources() != sources:
            raise ValueError("Reviewer packet or implementation changed")
        if claim_packet is not None:
            from .scientific_review import load_claim_packet
            if load_claim_packet(claim_packet_manifest, claim_packet_root, allow_evidence_read=True) != claim_packet:
                raise ValueError("Claim review packet changed")
    try:
        review_agent.atomic_json(root / "request.json", request)
        review_agent.atomic_json(root / "source-identity.json", sources)
        from .runtime import observe_runtime
        review_agent.atomic_json(root / "runtime.json", observe_runtime("local_supervised", detect_gpu=False))
        context = review_agent.review_context(packet)
        if claim_packet is not None:
            context.update(claim_review=review_agent.claim_review_context(claim_packet), claim_reviewer_id=claim_reviewer_id,
                claim_review_instructions="Return an actual independent claim_judgment alongside the advisory assessment. "
                    "Bind numerical assertions to exact diagnostic JSON pointers. Do not invent sources or qualification.")
        if len(json.dumps(context, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()) > policy.max_context_bytes:
            raise ValueError("review_context_budget_exhausted_no_dispatch")
        review_agent.atomic_json(root / "context.json", context)
        review_agent.atomic_json(root / "answer_schema.json", review_agent.review_schema(context))
        selected = root / "selector"
        selected.mkdir(mode=0o700)
        attempt = {"request_sha256": digest(request), "sequence": 1, "context": context, "attempt_path": str(root)}
        review_agent.atomic_json(root / "selection-reservation.json", {"sequence": 1, "maximum_provider_sessions": 2,
            "remaining_seconds": remaining(), "model_execution_authorized": allow_model_execution is True,
            **reservation_binding(configuration, attempt)})
        model_authority = review_agent.read_model_authority(root / "selection-reservation.json",
            configuration=configuration, attempt=attempt, maximum_authority=allow_model_execution)
        selection_timeout = min(remaining(), policy.session_timeout_seconds)
        # After entering a selector, zero attempts is no longer observable
        # unless the returned or persisted evidence establishes it.
        def select(disclosed):
            result.update(selector_entered=True, model_sessions_attempted=None)
            return selector.select(disclosed, selected, timeout_seconds=selection_timeout,
                memory_mb=policy.memory_mb, allow_model_execution=model_authority)
        choice = (evidence_delivery.transmit(_json_copy(context), select)
                  if evidence_delivery is not None else select(_json_copy(context)))
        if not isinstance(choice, Selection):
            raise InvalidSelection("Reviewer must return a Selection")
        decision = _json_copy(choice.action)
        review_agent.atomic_json(root / "selection.json", {"decision": decision, "evidence": choice.evidence})
        for field in ("model_sessions_attempted", "tokens", "cost_usd", "actual_model"):
            result[field] = choice.evidence.get(field)
        unchanged()
        import jsonschema
        try:
            _public_only(decision)
            jsonschema.Draft202012Validator(review_agent.review_schema(context)).validate(decision)
            _assessment({key: value for key, value in decision.items() if key not in {"kind", "claim_judgment"}}, packet)
            if claim_packet is not None:
                from .scientific_judgment import parse_claim_judgment, validate_judgment
                validate_judgment(claim_packet, parse_claim_judgment(decision["claim_judgment"]))
        except (ValueError, TypeError, jsonschema.ValidationError) as error:
            raise InvalidSelection("Invalid scientific review response: " + type(error).__name__) from error
        result["status"] = "review_selected"
    except InvalidSelection as error:
        result.update(status="invalid_review", reason=str(error)[:2000], error_type=type(error).__name__)
    except (Exception, KeyboardInterrupt) as error:
        result.update(status="infrastructure_failure", reason=str(error)[:2000], error_type=type(error).__name__)
    finally:
        if (root / "selector/evidence.json").is_file():
            try:
                from .episode_codex import _read_evidence
                evidence = _read_evidence(root / "selector/evidence.json")
                for field in ("model_sessions_attempted", "tokens", "cost_usd", "actual_model"):
                    result[field] = evidence.get(field)
            except (OSError, TypeError, ValueError, AttributeError):
                result.update(status="infrastructure_failure", reason="reviewer_evidence_unreadable",
                              model_sessions_attempted=None, tokens=None, cost_usd=None, actual_model=None)
        result.update(wall_seconds=review_agent.time.monotonic()-started, evidence_capture_complete=True)
        for path in sorted(root.iterdir()):
            if path.name == "audit":
                continue
            try:
                audit.add_artifact(path, max_bytes=20_000_000, max_entries=5000)
            except (OSError, ValueError, RuntimeError) as error:
                result.update(status="infrastructure_failure", reason="reviewer_evidence_capture_failed:"+type(error).__name__, evidence_capture_complete=False)
        review_agent.atomic_json(root / "result.json", result)
        audit.add_artifact(root / "result.json")
        audit.append("analysis.scientific_review_selection", {"status": result["status"], "packet_root": expected_root, "analysis_executed": False})
        receipt = audit.finalize(analysis_fingerprint=digest(request))
        review_agent.atomic_json(root / "selection-root-receipt.json", receipt)
    result["selection_receipt"] = receipt
    if result["status"] == "review_selected":
        publication = ExecutionDAG(root / "publication-audit", actor_id="scientific-review-publication-coordinator")
        try:
            unchanged()
            origin = {"manifest_path": receipt["manifest_path"], "root_hash": receipt["root_hash"], "packet_root": expected_root,
                      "context_sha256": digest(context), "decision_sha256": digest(decision)}
            result["review_receipt"] = review_agent.record_scientific_review(root / "review", packet_manifest, expected_root=expected_root,
                reviewer_label=reviewer_label, assessment={k:v for k,v in decision.items() if k not in {"kind", "claim_judgment"}},
                qualification_statement="Automated or recorded suggestion; independent scientific qualification is not established.",
                independence_statement="A separate selection does not establish independent scientific judgment.",
                allow_review=True, selection_origin=origin)
            if claim_packet is not None:
                from .scientific_judgment import parse_claim_judgment
                from .scientific_review import record_claim_judgment
                result["claim_review_receipt"] = record_claim_judgment(root / "claim-review", claim_packet_manifest,
                    expected_root=claim_packet_root, judgment=parse_claim_judgment(decision["claim_judgment"]),
                    allow_evidence_read=True, allow_review=True)
            result["status"] = "review_recorded"
        except (Exception, KeyboardInterrupt) as error:
            result.update(status="infrastructure_failure", reason=str(error)[:2000], error_type=type(error).__name__, review_receipt=None)
        result["wall_seconds"] = review_agent.time.monotonic()-started
        review_agent.atomic_json(root / "publication.json", result)
        publication.add_artifact(root / "publication.json")
        publication.append("analysis.scientific_review_publication", {"status": result["status"], "selection_root": receipt["root_hash"],
            "review_root": (result["review_receipt"] or {}).get("root_hash")})
        result["receipt"] = publication.finalize(analysis_fingerprint=digest(request))
    else:
        result["receipt"] = receipt
    review_agent.atomic_json(root / "root-receipt.json", result["receipt"])
    return return_delivery.release(result) if return_delivery is not None else result
