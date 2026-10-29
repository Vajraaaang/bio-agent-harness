"""Controlled implementation gaps and explicit late-evidence policy.

The workflow ledger retains the returned branch decision. This adapter drafts
inert source only; it never starts numerical execution, installs software,
changes a method's trust, or substitutes availability for scientific suitability.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path

from devharness_core.provenance import canonical_json


def _digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 8000


def reassessment_declaration(state, *, reason):
    """Bind an operator's reassessment intent to the still-unmodified decision."""
    if not _text(reason):
        raise ValueError("Late evidence reassessment needs an explicit bounded reason")
    return {"kind": "reassess", "reason": reason,
        "candidate_sha256": _digest(state.get("scientific_candidates")),
        "source_sha256": _digest(state.get("sources", []))}


def require_late_evidence_policy(state, *, operation, reassessment=None):
    """Check before mutation; callers retain declarations and original decisions.

    A reassessment declaration authorizes recording new evidence, not attributing
    it to old candidates. Downstream capability/design dispatch must wait for an
    explicit reassessment bound to that evidence. A new cycle needs a new plan.
    """
    mutable = {"retrieve_evidence", "resolve_capabilities", "design_trials", "declare_trial",
        "draft_implementation", "reassess_candidates", "generate_candidates"}
    if operation not in mutable:
        raise ValueError("Unknown evidence/comparison mutation")
    if state.get("results"):
        raise ValueError("Comparisons are frozen after results; use a new planning cycle")
    if operation != "retrieve_evidence" or state.get("scientific_candidates") is None:
        return {"status": "allowed"}
    if state.get("experiments"):
        raise ValueError("Source evidence is frozen after trial declaration; use a new planning cycle")
    if (type(reassessment) is not dict or set(reassessment) != {
            "kind", "reason", "candidate_sha256", "source_sha256"}
            or reassessment.get("kind") != "reassess" or not _text(reassessment.get("reason"))):
        raise ValueError("Late evidence requires an explicit reassessment declaration or a new planning cycle")
    if (reassessment["candidate_sha256"] != _digest(state["scientific_candidates"])
            or reassessment["source_sha256"] != _digest(state.get("sources", []))):
        raise ValueError("Stale reassessment binding differs from frozen candidates or sources")
    return {"status": "reassessment_required", "declaration": deepcopy(reassessment),
        "previous_scientific_candidates": deepcopy(state["scientific_candidates"]),
        "previous_sources": deepcopy(state.get("sources", []))}


def implementation_review_binding(methods, method_version_id, run_id):
    """Prepare exact diagnostic evidence for review, granting no acceptance.

    The reviewer must receive the actual source and interface, not merely their
    hashes or the generated method's assertion that it implements an algorithm.
    """
    method = methods.method(method_version_id)
    run = methods.get("run", run_id)
    if run.get("method_version_id") != method_version_id or not methods.provenance(run_id)["valid"]:
        raise ValueError("Implementation review needs an exact verified method run")
    source = methods.artifact_path(method["source_artifact"])
    if method["environment_artifact"] is not None:
        methods.artifact_path(method["environment_artifact"])
    contract = method["contract"]
    return {"method_version_id": method_version_id,
        "source_sha256": contract["implementation"]["source_sha256"],
        "source_code": source.read_text(encoding="utf-8"), "interface": deepcopy(contract),
        "environment_lock_sha256": contract["environment"]["lock_sha256"],
        "run_id": run_id, "run_root": run["evidence"]["root_hash"], "validation": deepcopy(run["validation"])}


def verify_implementation_validation(methods, method_version_id, record):
    """Verify existing OCI numerical evidence and an exact correspondence review.

    Only the existing profile comparator currently certifies arithmetic fidelity.
    Other generated algorithms remain unvalidated here instead of silently
    treating structural JSON checks or candidate self-reports as numerical proof.
    The existing claim receipt retains unestablished scientific qualification.
    """
    fields = {"run_id", "packet_manifest", "packet_root", "judgment_manifest", "judgment_root"}
    if type(record) is not dict or set(record) != fields:
        raise ValueError("Implementation validation requires exact retained execution and claim-review references")
    method = methods.method(method_version_id)
    contract = method["contract"]
    for identity in (method["source_artifact"], method["environment_artifact"]):
        if identity is not None:
            methods.artifact_path(identity)
    run = methods.get("run", record["run_id"])
    execution = run.get("execution") or {}
    if (contract["implementation"]["kind"] != "oci" or run.get("backend") != "local_docker"
            or execution.get("runtime") != "local_docker" or execution.get("image") != contract["environment"]["image"]
            or run.get("method_version_id") != method_version_id):
        raise ValueError("Validation needs actual matching local_docker OCI execution evidence")
    if run.get("status") != "succeeded" or methods.provenance(run["run_id"])["valid"] is not True:
        raise ValueError("Implementation validation execution failed or lost immutable provenance")
    validation = run.get("validation") or {}
    arithmetic = validation.get("arithmetic_fidelity") or {}
    profile = contract.get("diagnostic_policy", {}).get("profile")
    if (validation.get("passed") is not True or validation.get("comparator") != "observation_diagnostic"
            or arithmetic.get("verified") is not True or profile is None
            or arithmetic.get("profile_sha256") != _digest(profile)
            or arithmetic.get("input_sha256") != validation.get("input_sha256")):
        raise ValueError("Implementation numerical behavior lacks independently recomputed arithmetic evidence")
    for identity in (*run["inputs"].values(), *run["outputs"].values()):
        methods.artifact_path(identity)
    from .scientific_review import load_claim_packet, load_claim_judgment
    from .scientific_judgment import validate_judgment, verified_artifacts
    packet = load_claim_packet(record["packet_manifest"], record["packet_root"], allow_evidence_read=True)
    judgment = load_claim_judgment(record["judgment_manifest"], record["judgment_root"],
        packet_manifest=record["packet_manifest"], packet_root=record["packet_root"], allow_evidence_read=True)
    dimensions = validate_judgment(packet, judgment)
    checks = {check.check: check for check in packet.objective_checks}
    if (packet.input_root != method_version_id or packet.execution != "completed"
            or packet.objective_validity != "verified"
            or dimensions["scientific_judgment"] != "supported" or dimensions["failed_checks"]
            or any(name not in checks or checks[name].status != "verified"
                for name in ("input_integrity", "citation", "statistics", "execution"))
            or not judgment.numerical_assertions):
        raise ValueError("Source correspondence and numerical behavior require a substantive bound claim review")
    artifacts = verified_artifacts(packet)
    binding = implementation_review_binding(methods, method_version_id, run["run_id"])
    bound_ids = {identity for identity, value in artifacts.items() if value == binding}
    if not bound_ids or not bound_ids <= set(judgment.evidence_ids):
        raise ValueError("Correspondence review lacks exact source, environment and numerical-run binding")
    citations = set(contract.get("provenance", {}).get("citations", []))
    sources = {item.evidence_id for item in packet.evidence if item.kind == "source"
        and item.source.url in citations and item.source.access in {"documentation", "full_text"}}
    if (not sources.intersection(judgment.evidence_ids)
            or not sources.intersection(checks["citation"].evidence_ids)
            or not bound_ids.intersection(checks["citation"].evidence_ids)
            or not bound_ids.intersection(checks["statistics"].evidence_ids)
            or not any(item.evidence_id in bound_ids for item in judgment.numerical_assertions)):
        raise ValueError("Correspondence review must assess retrieved source and actual bound numerical values")
    return {"status": "validated_for_declared_checks", "method_version_id": method_version_id,
        "run_id": run["run_id"], "run_root": run["evidence"]["root_hash"],
        "packet_root": record["packet_root"], "judgment_root": record["judgment_root"],
        "qualification_status": "not_established", "independence_verified": False,
        "implementation_trust": contract["implementation_trust"],
        "scientific_validity_established": False, "execution_granted": False, "promotion_granted": False}


def perform_implementation(planner, session_id, selector, *, actor, requests=(), allow_model_execution=False):
    """Record a gap/no-op, or draft exactly authorized OCI interfaces.

    An unavailable alternative does not block a scoped descriptive route.
    A newly drafted alternative cannot be relied on before retained numerical
    and correspondence checks. The graph ledger owns immutable no-op/replay
    decisions; this helper also reuses already-retained identical drafts.
    """
    if not isinstance(requests, (list, tuple)) or len(requests) > 16:
        raise ValueError("Use bounded explicit implementation requests")
    state = planner.get(session_id)
    require_late_evidence_policy(state, operation="draft_implementation")
    if state.get("experiments"):
        raise ValueError("Implementation branch is frozen after trial declaration")
    candidates = state.get("candidates")
    if not candidates:
        raise ValueError("Resolve scientific candidates before implementation branching")
    by_name = {candidate["name"]: candidate for candidate in candidates}
    supplied = {}
    allowed_fields = {"candidate", "interface", "environment_lock", "justification", "authorized", "validation"}
    for value in requests:
        if (type(value) is not dict or set(value) - allowed_fields or not isinstance(value.get("candidate"), str)
                or value["candidate"] not in by_name or value["candidate"] in supplied):
            raise ValueError("Implementation requests must name distinct resolved candidates")
        supplied[value["candidate"]] = value
    drafts, unavailable, available, validation_required, validations = [], [], [], [], []
    retained = {item["candidate"]: item for item in state.get("implementation_drafts", [])}
    for name, candidate in by_name.items():
        value = supplied.get(name)
        previous = retained.get(name)
        if previous is None and candidate["method_version_id"] is not None:
            if value is not None:
                raise ValueError("An available implementation is not a missing-implementation gap")
            available.append({"candidate": name, "method_version_id": candidate["method_version_id"]})
            continue
        reason = None
        if value is None:
            reason = "no_authorized_implementation_request"
        elif value.get("authorized") is not True:
            reason = "implementation_drafting_not_authorized"
        elif not _text(value.get("justification")):
            reason = "implementation_not_justified"
        elif candidate.get("applicability") != "eligible":
            reason = "scientific_preconditions_unresolved_or_inapplicable"
        elif not isinstance(value.get("interface"), dict) or value["interface"].get("implementation", {}).get("kind") != "oci":
            reason = "unsupported_interface_requires_oci_boundary"
        if previous is not None and (reason is not None or previous["interface_sha256"] != _digest(value["interface"])
                or previous["justification"] != value["justification"]):
            raise ValueError("Retained implementation branch is immutable; use a new planning cycle")
        if reason is not None:
            unavailable.append({"candidate": name, "reason": reason,
                "availability": candidate["availability"], "method_version_id": candidate["method_version_id"]})
            continue
        lock = value.get("environment_lock")
        if not isinstance(lock, (str, Path)) or not Path(lock).is_absolute():
            raise ValueError("Implementation environment lock needs an absolute path")
        if previous is None:
            previous = planner.draft_implementation(session_id, name, selector, value["interface"], lock,
                actor=actor, justification=value["justification"], allow_model_execution=allow_model_execution)
        else:
            # Validate retained source and lock identity even when no selector is invoked.
            method = planner.methods.method(previous["method_version_id"])
            planner.methods.artifact_path(method["source_artifact"])
            planner.methods.artifact_path(method["environment_artifact"])
            from devharness_core.provenance import hash_artifact
            if hash_artifact(lock, max_bytes=1_000_000)["sha256"] != method["contract"]["environment"]["lock_sha256"]:
                raise ValueError("Retained implementation environment lock changed")
        drafts.append(deepcopy(previous))
        if value.get("validation") is None:
            validation_required.append({"candidate": name, "method_version_id": previous["method_version_id"]})
        else:
            validations.append(verify_implementation_validation(planner.methods, previous["method_version_id"], value["validation"]))
    result = {"format": "devbench.workflow-implementation/1",
        "status": "needs_validation" if validation_required else "unavailable" if unavailable else "ready",
        "available": available, "unavailable": unavailable, "drafts": drafts,
        "validation_required": validation_required, "validations": validations,
        "execution_granted": False, "promotion_granted": False}
    result["decision_sha256"] = _digest(result)
    return result
