"""Deterministic investigation admission and evidence-only reconciliation.

The coordinator consumes captured nominations. This module never invokes a
model, selects a scientific winner, runs a fit, or grants execution authority.
"""
from copy import deepcopy
import json
import re
import uuid
import unicodedata

from devharness_core.provenance import canonical_json
from .workflow_state import digest, planning_binding


TRIGGER_FORMAT = "devbench.rna-investigation-trigger/1"
DECISION_FORMAT = "devbench.rna-investigation-decision/1"
BUNDLE_FORMAT = "devbench.rna-investigation-bundle/1"
ASSIGNMENT_POLICY = "devbench.rna-scoped-assignment/1"
MAX_PROJECTION_BYTES = 24_000
INVESTIGATION_ARTIFACT_TYPES = {"rna_investigation_packet", "rna_investigation_result", "rna_investigation_capture",
    "rna_investigation_usage", "rna_investigation_reconciliation"}
AXES = {
    "pre_fit_methods": {"temporal_shape_or_complexity", "valid_nuisance_model", "objective_tradeoff"},
    "post_fit_explanation": {"composition_vs_intrinsic", "technical_vs_biological", "alternative_biological_context"},
}
TRIGGER_KEYS = {"format", "stage", "action_intent", "scope_binding", "question_ref", "requirement_refs",
    "prerequisite_checks", "fixed_procedure_requirement_ref", "nomination_ref", "ambiguity_items",
    "evidence_ready", "prior_fork_count", "budget_policy_ref", "admissible_child_count"}


def _ref(value):
    if (type(value) is not dict or set(value) != {"artifact_id", "sha256", "artifact_type", "size_bytes"}
            or type(value["artifact_id"]) is not str or not value["artifact_id"] or len(value["artifact_id"]) > 300
            or type(value["sha256"]) is not str or re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is None
            or value["artifact_type"] not in {"json", "file", "table", "anndata"} | INVESTIGATION_ARTIFACT_TYPES
            or type(value["size_bytes"]) is not int or value["size_bytes"] < 0):
        raise ValueError("Use an exact immutable artifact reference")
    return value


def _refs(value, maximum=128):
    if type(value) is not list or len(value) > maximum:
        raise ValueError("Use bounded immutable artifact references")
    for item in value:
        _ref(item)
    if len({item["artifact_id"] for item in value}) != len(value):
        raise ValueError("Duplicate artifact reference")
    return value


def _sha(value):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("Use an exact SHA-256 binding")


def validate_trigger_features(value):
    if type(value) is not dict or set(value) != TRIGGER_KEYS or value["format"] != TRIGGER_FORMAT:
        raise ValueError("Use the exact versioned trigger features; numerical outcomes cannot choose a fork")
    if len(canonical_json(value)) > 24_000:
        raise ValueError("Trigger features exceed their byte budget")
    if value["stage"] not in AXES or value["action_intent"] not in {"retain", "diagnostic_flagging", "exclude", "infer"}:
        raise ValueError("Unsupported investigation stage or action")
    _sha(value["scope_binding"])
    for key in ("question_ref", "budget_policy_ref"):
        _ref(value[key])
    _refs(value["requirement_refs"], 64)
    for key in ("fixed_procedure_requirement_ref", "nomination_ref"):
        if value[key] is not None:
            _ref(value[key])
    checks = value["prerequisite_checks"]
    if type(checks) is not list or not 1 <= len(checks) <= 32:
        raise ValueError("Use bounded coordinator prerequisite checks")
    ids = set()
    for check in checks:
        if (type(check) is not dict or set(check) != {"check_id", "status", "evidence_ref", "action_scope"}
                or type(check["check_id"]) is not str or not check["check_id"] or len(check["check_id"]) > 120
                or check["check_id"] in ids
                or check["status"] not in {"satisfied", "failed", "unknown", "not_applicable"}
                or check["action_scope"] != value["action_intent"]):
            raise ValueError("Prerequisite checks must have exact coordinator IDs, status, and action scope")
        ids.add(check["check_id"])
        _ref(check["evidence_ref"])
    items = value["ambiguity_items"]
    if type(items) is not list or len(items) > 2:
        raise ValueError("At most two ambiguity items are supported")
    ids = set()
    for item in items:
        if (type(item) is not dict or set(item) != {"id", "axis", "hypothesis_ids", "evidence_refs",
                "missing_information_status", "discriminating_criterion"}
                or type(item["id"]) is not str or not item["id"] or len(item["id"]) > 100 or item["id"] in ids):
            raise ValueError("Use exact bounded ambiguity items")
        ids.add(item["id"])
        if item["axis"] not in AXES[value["stage"]]:
            raise ValueError("Ambiguity axis is incompatible with the selected stage")
        hypotheses = item["hypothesis_ids"]
        if (type(hypotheses) is not list or len(hypotheses) != 2 or len(set(hypotheses)) != 2
                or any(type(v) is not str or not v or len(v) > 120 for v in hypotheses)):
            raise ValueError("An ambiguity requires two explicit distinct hypothesis IDs")
        _refs(item["evidence_refs"], 16)
        criterion = item["discriminating_criterion"]
        if (type(criterion) is not str or not criterion or len(criterion) > 300
                or any(term in criterion.casefold() for term in
                       ("p_value", "q_value", "significance", "discovery_count", "unseen_outcome", "heldout_answer"))):
            raise ValueError("Use an observable discriminating criterion, never significance or unseen outcomes")
        if item["missing_information_status"] not in {"sufficient", "unknown", "critical_unknown"}:
            raise ValueError("Declare the ambiguity's missing information status")
    if (type(value["evidence_ready"]) is not bool or type(value["prior_fork_count"]) is not int
            or value["prior_fork_count"] < 0 or type(value["admissible_child_count"]) is not int
            or value["admissible_child_count"] not in {0, 1, 2}):
        raise ValueError("Use observed readiness, retained fork count, and a frozen 0/1/2 allocation")
    return deepcopy(value)


def validate_investigation_decision(value):
    if (type(value) is not dict or set(value) != {"format", "features", "policy_digest", "status", "reason",
            "allocated_children", "selected_ambiguity_ids"} or value["format"] != DECISION_FORMAT
            or value["status"] not in {"triggered", "not_triggered", "blocked"}
            or type(value["reason"]) is not str or not value["reason"] or len(value["reason"]) > 500
            or type(value["allocated_children"]) is not int or value["allocated_children"] not in {0, 1, 2}):
        raise ValueError("Use the exact versioned frozen investigation decision")
    features = validate_trigger_features(value["features"])
    _sha(value["policy_digest"])
    selected = value["selected_ambiguity_ids"]
    if (type(selected) is not list or len(selected) != value["allocated_children"] or len(set(selected)) != len(selected)
            or any(item not in {row["id"] for row in features["ambiguity_items"]} for item in selected)
            or (value["status"] == "triggered") != bool(value["allocated_children"])):
        raise ValueError("The frozen decision allocation differs from its admitted ambiguity items")
    return deepcopy(value)


def decide_investigation(plan, diagnostics, *, policy):
    """Apply frozen observable rules; captures must also be verified at dispatch.

    ``diagnostics`` is assembled by the coordinator from immutable references,
    not a model's confidence or self-report. No hidden classifier is called.
    """
    if (type(policy) is not dict or policy.get("format") != "devbench.rna-investigation-policy/1"
            or policy.get("stage") not in AXES or type(policy.get("allocation")) is not int
            or policy["allocation"] not in {0, 1, 2}):
        raise ValueError("Declare a frozen investigation stage and 0/1/2 allocation")
    expected = {"features", "nomination", "requirement_records", "scope_supported", "capability_supported",
                "parent_reconciliation_reserved", "unresolved_dispatch"}
    if type(diagnostics) is not dict or set(diagnostics) != expected:
        raise ValueError("Use exact coordinator investigation diagnostics")
    for key in expected - {"features", "nomination", "requirement_records"}:
        if type(diagnostics[key]) is not bool:
            raise ValueError("Coordinator routing diagnostics require exact booleans")
    features = validate_trigger_features(diagnostics["features"])
    if features["scope_binding"] != planning_binding(plan) or features["stage"] != policy["stage"]:
        raise ValueError("Investigation stage or scope binding is stale")
    if features["budget_policy_ref"] != policy.get("budget_policy_ref"):
        raise ValueError("Investigation budget policy binding is stale")

    def answer(status, reason, count=0):
        return validate_investigation_decision({"format": DECISION_FORMAT, "features": features,
            "policy_digest": digest(policy), "status": status, "reason": reason, "allocated_children": count,
            "selected_ambiguity_ids": [row["id"] for row in features["ambiguity_items"][:count]]})

    required = {"input_integrity", "representation", "join"}
    if features["action_intent"] in {"infer", "exclude"}:
        required |= {"biological_units", "contrast", "rank"}
    if any(row["axis"] == "temporal_shape_or_complexity" for row in features["ambiguity_items"]):
        required.add("chronology")
    checks = {row["check_id"]: row for row in features["prerequisite_checks"]}
    if not diagnostics["scope_supported"] or not required <= set(checks):
        return answer("blocked", "Unsupported scope or missing mandatory coordinator prerequisites")
    if any(row["status"] in {"failed", "unknown"} for row in checks.values()):
        return answer("blocked", "A failed or critical unknown prerequisite blocks this action")
    if features["prior_fork_count"] or diagnostics["unresolved_dispatch"]:
        return answer("not_triggered", "A previous fork or unresolved dispatch prevents a new fork")
    if not diagnostics["capability_supported"] or not diagnostics["parent_reconciliation_reserved"]:
        return answer("not_triggered", "The selected capability or reserved parent reconciliation capacity is unavailable")
    fixed = features["fixed_procedure_requirement_ref"]
    if fixed is not None:
        records = diagnostics["requirement_records"]
        matched = [row for row in records if type(row) is dict and row.get("ref") == fixed]
        if (fixed not in features["requirement_refs"] or len(matched) != 1
                or matched[0].get("requirement") not in plan.get("user_parameter_requirements", [])
                or matched[0]["requirement"].get("name") != "fixed_procedure"):
            raise ValueError("Fixed procedure requires an exact explicit matching requirement record")
        if features["stage"] == "pre_fit_methods":
            return answer("not_triggered", "The analytical procedure is explicitly fixed by a matching requirement")
    if not features["nomination_ref"] or not features["ambiguity_items"] or not features["evidence_ready"]:
        return answer("not_triggered", "No captured admissible nomination with stage-compatible evidence exists")
    nomination = diagnostics["nomination"]
    nomination_keys = {"parent_session_id", "selection_attempt", "context_sha256", "capture_ref", "usage_ref",
                       "ambiguity_items_sha256"}
    if (type(nomination) is not dict or set(nomination) != nomination_keys
            or nomination["parent_session_id"] != plan["session_id"]
            or nomination["ambiguity_items_sha256"] != digest(features["ambiguity_items"])):
        raise ValueError("The retained nomination does not bind the exact ambiguity observations")
    phase = "method_scientific_planning" if features["stage"] == "pre_fit_methods" else "method_preliminary_explanation"
    history = [row for row in plan.get("selection_history", []) if row.get("attempt") == nomination["selection_attempt"]]
    if (len(history) != 1 or history[0].get("phase") != phase or history[0].get("status") != "accepted"
            or history[0].get("context_sha256") != nomination["context_sha256"]
            or history[0].get("model_capture_ref", history[0].get("evidence", {}).get("capture_ref")) != nomination["capture_ref"]
            or history[0].get("model_usage_ref", history[0].get("evidence", {}).get("usage_ref")) != nomination["usage_ref"]):
        raise ValueError("Nomination requires an actual captured and charged accepted parent invocation")
    _ref(nomination["capture_ref"])
    _ref(nomination["usage_ref"])
    if any(row["missing_information_status"] != "sufficient" or not row["evidence_refs"]
           for row in features["ambiguity_items"]):
        return answer("not_triggered", "Missing critical information is not an eligible scientific ambiguity")
    count = min(policy["allocation"], features["admissible_child_count"])
    if count > len(features["ambiguity_items"]):
        return answer("not_triggered", "The frozen policy has too few distinct admissible investigations")
    return answer("triggered" if count else "not_triggered", "Frozen observable allocation admitted" if count else
                  "Frozen policy admits zero independent children", count)


def reduce_investigation_refs(left, right):
    """ID-keyed fan-in: identical returns are harmless; conflicting ones fail."""
    if type(left) is not dict or type(right) is not dict:
        raise ValueError("Use an exact ID-keyed reference collection")
    output = deepcopy(left)
    for identity, item in right.items():
        if (type(identity) is not str or not identity or len(identity) > 128
                or type(item) is not dict or set(item) != {"result_ref", "status"}
                or item["status"] not in {"completed", "abstained", "failed", "incomplete"}):
            raise ValueError("Use exact bounded terminal child references")
        if item["result_ref"] is not None:
            _ref(item["result_ref"])
        if identity in output and output[identity] != item:
            raise ValueError("Conflicting child result reference for one investigation ID")
        output[identity] = deepcopy(item)
    if len(output) > 2:
        raise ValueError("At most two bounded child references are supported")
    # Validate retained left values too, including when right is empty.
    for identity, item in output.items():
        if (type(identity) is not str or not identity or len(identity) > 128 or type(item) is not dict
                or set(item) != {"result_ref", "status"}
                or item["status"] not in {"completed", "abstained", "failed", "incomplete"}):
            raise ValueError("Use exact bounded terminal child references")
        if item["result_ref"] is not None:
            _ref(item["result_ref"])
    return {key: output[key] for key in sorted(output)}


def retain_investigation_json(methods, value, *, actor, max_bytes=64_000, artifact_type=None):
    """Import a bounded canonical artifact through the existing MethodService."""
    encoded = canonical_json(value)
    if len(encoded) > max_bytes:
        raise ValueError("Investigation artifact exceeds its byte budget; provide an admissible compact view")
    artifact_type = artifact_type or {"devbench.rna-investigation-packet/1": "rna_investigation_packet",
        "devbench.rna-investigation-result/1": "rna_investigation_result",
        BUNDLE_FORMAT: "rna_investigation_reconciliation"}.get(value.get("format") if type(value) is dict else None, "json")
    if artifact_type not in {"json"} | INVESTIGATION_ARTIFACT_TYPES:
        raise ValueError("Unsupported investigation artifact type")
    from .method_executor import _path
    directory = _path(methods.root / "investigation-imports")
    directory.mkdir(mode=0o700, exist_ok=True)
    path = _path(directory / (uuid.uuid4().hex + ".json"))
    try:
        with path.open("xb") as stream:
            stream.write(encoded)
        record = methods.import_artifact(path, artifact_type=artifact_type, actor=actor)
        return {key: record[key] for key in ("artifact_id", "sha256", "artifact_type", "size_bytes")}
    finally:
        path.unlink(missing_ok=True)


def read_investigation_json(methods, artifact_ref, *, max_bytes=64_000):
    """Resolve immutable content without writes, tools, retrieval, or fitting."""
    _ref(artifact_ref)
    if artifact_ref["artifact_type"] not in {"json"} | INVESTIGATION_ARTIFACT_TYPES or artifact_ref["size_bytes"] > max_bytes:
        raise ValueError("Investigation reference exceeds its JSON byte budget")
    record = methods.get("artifact", artifact_ref["artifact_id"])
    if {key: record[key] for key in artifact_ref} != artifact_ref:
        raise ValueError("Investigation artifact reference differs from its manifest")
    path = methods.artifact_path(artifact_ref["artifact_id"])
    with path.open("rb") as stream:
        encoded = stream.read(max_bytes + 1)
    if len(encoded) != artifact_ref["size_bytes"] or len(encoded) > max_bytes:
        raise ValueError("Investigation artifact byte budget or size differs")
    try:
        value = json.loads(encoded)
        if canonical_json(value) != encoded:
            raise ValueError("Investigation artifact requires finite canonical JSON")
    except (ValueError, TypeError, RecursionError) as error:
        raise ValueError("Investigation artifact is not finite canonical JSON") from error
    return value


def _task_for_assignment(assignment, artifact_id):
    scope = ("Report independent methods, assumptions and comparisons; input audits only."
             if assignment["stage"] == "pre_fit_methods" else
             "Report competing explanations and discriminating evidence; verified outputs only.")
    item = assignment["selected_ambiguity"]
    task = f"{item['axis']}; {item['discriminating_criterion']}. {scope} brief={artifact_id}"
    if len(task) > 300:
        raise ValueError("Scoped investigator brief exceeds 300 characters; supply an admissible compact criterion")
    return task


def compose_investigation_task(methods, packet, decision, *, selected_item_id, actor):
    """Compose a neutral scoped task from the already charged lead nomination.

    The short task carries an opaque immutable assignment reference. Objective,
    requirements and evidence are already present in the neutral packet; its
    normal transport digest binds the final task without a self-referential hash.
    No parent candidate, ranking, narrative, or sibling content is copied.
    """
    from .rna_investigation_contract import validate_investigation_packet
    packet = validate_investigation_packet(packet)
    decision = validate_investigation_decision(decision)
    items = [item for item in decision["features"]["ambiguity_items"] if item["id"] == selected_item_id]
    if (decision["status"] != "triggered" or selected_item_id not in decision["selected_ambiguity_ids"] or len(items) != 1
            or decision["features"]["stage"] != packet["stage"] or decision["policy_digest"] != packet["bindings"]["policy_digest"]):
        raise ValueError("Scoped assignment must select one exact admitted scientific ambiguity")
    criterion = unicodedata.normalize("NFKC", items[0]["discriminating_criterion"]).casefold()
    before = methods.get("method_plan_snapshot", packet["bindings"]["plan_hash"])
    names = [unicodedata.normalize("NFKC", candidate["name"]).casefold()
             for candidate in before.get("scientific_candidates", [])]
    if (re.search(r"\b(prefer(?:red)?|ranking|ranked|sibling|transcript|selection_history)\b", criterion)
            or any(name and name in criterion for name in names)):
        raise ValueError("A scoped brief must remain neutral; preferred choices, ranking and sibling material are forbidden")
    assignment = {"format": ASSIGNMENT_POLICY, "attribution": "coordinator_composed_from_charged_lead_nomination",
        "parent_session_id": packet["parent_session_id"], "investigation_id": packet["investigation_id"], "stage": packet["stage"],
        "selected_ambiguity": deepcopy(items[0]), "nomination_ref": deepcopy(decision["features"]["nomination_ref"]),
        "question_record_digest": digest(packet["question_record"]), "requirements_digest": digest(packet["requirement_records"]),
        "packet_context_digest": digest({**packet, "task": ""}), "bindings": deepcopy(packet["bindings"]),
        "return_schema_digest": packet["return_schema_digest"]}
    # Check the engineering text bound before retaining any artifact.
    _task_for_assignment(assignment, "0" * 64)
    reference = retain_investigation_json(methods, assignment, actor=actor, max_bytes=16_000)
    return validate_investigation_packet({**packet, "task": _task_for_assignment(assignment, reference["artifact_id"])})


def verify_investigation_assignment(methods, packet, plan, *, ledger=None):
    """Verify private assignment provenance; expose no parent capture content."""
    from .rna_investigation_contract import validate_investigation_packet
    packet = validate_investigation_packet(packet)
    marker = "brief="
    retained = plan.get("model_campaign")
    if retained is None:
        if marker in packet["task"]:
            raise ValueError("Scoped assignment lacks its retained shared campaign")
        return None
    from .workflow_operations import WorkflowOperationLedger
    ledger = ledger or WorkflowOperationLedger(retained["ledger_path"], readonly=True)
    campaign = ledger.get_campaign(retained["campaign_id"])
    frozen = campaign["identity"].get("investigation_policy", {})
    if marker not in packet["task"]:
        if frozen.get("assignment_policy") == ASSIGNMENT_POLICY:
            raise ValueError("This independent workflow requires a scoped investigator brief")
        return None  # Existing standalone packet callers retain their contract.
    identity = packet["task"].rsplit(marker, 1)[1]
    if re.fullmatch(r"[0-9a-f]{64}", identity) is None:
        raise ValueError("Scoped assignment reference is malformed")
    record = methods.get("artifact", identity)
    reference = {key: record[key] for key in ("artifact_id", "sha256", "artifact_type", "size_bytes")}
    assignment = read_investigation_json(methods, reference, max_bytes=16_000)
    keys = {"format", "attribution", "parent_session_id", "investigation_id", "stage", "selected_ambiguity", "nomination_ref",
            "question_record_digest", "requirements_digest", "packet_context_digest", "bindings", "return_schema_digest"}
    if (type(assignment) is not dict or set(assignment) != keys or assignment["format"] != ASSIGNMENT_POLICY
            or assignment["attribution"] != "coordinator_composed_from_charged_lead_nomination"
            or any(assignment[key] != packet[key] for key in ("parent_session_id", "investigation_id", "stage", "return_schema_digest"))
            or assignment["bindings"] != packet["bindings"]
            or assignment["question_record_digest"] != digest(packet["question_record"])
            or assignment["requirements_digest"] != digest(packet["requirement_records"])
            or assignment["packet_context_digest"] != digest({**packet, "task": ""})
            or _task_for_assignment(assignment, identity) != packet["task"]):
        raise ValueError("Scoped assignment differs from its exact neutral packet, objective, requirements or bindings")
    logical = ledger.get(packet["parent_session_id"], "investigation_decision", 0)
    if logical is None or logical["status"] != "completed":
        raise ValueError("Scoped assignment has no retained completed admission decision")
    decision = validate_investigation_decision(logical["result"]["decision"])
    item = assignment["selected_ambiguity"]
    if (item not in decision["features"]["ambiguity_items"] or item["id"] not in decision["selected_ambiguity_ids"]
            or assignment["nomination_ref"] != decision["features"]["nomination_ref"]
            or decision["policy_digest"] != packet["bindings"]["policy_digest"]):
        raise ValueError("Scoped assignment changed the exact selected charged lead nomination")
    nomination = read_investigation_json(methods, assignment["nomination_ref"], max_bytes=8000)
    rows = [row for row in plan["selection_history"] if row["attempt"] == nomination["selection_attempt"]]
    phase = "method_scientific_planning" if packet["stage"] == "pre_fit_methods" else "method_preliminary_explanation"
    if (len(rows) != 1 or rows[0]["status"] != "accepted" or rows[0]["phase"] != phase
            or rows[0]["context_sha256"] != nomination["context_sha256"]
            or rows[0].get("model_capture_ref") != nomination["capture_ref"]
            or rows[0].get("model_usage_ref") != nomination["usage_ref"]):
        raise ValueError("Scoped assignment is not bound to its actual accepted lead invocation")
    receipt = ledger.get_model_reservation(rows[0]["model_reservation_ref"])
    usage = read_investigation_json(methods, nomination["usage_ref"], max_bytes=24_000)
    if (receipt["status"] != "completed" or receipt["capture"] is None
            or receipt["capture"]["capture_ref"] != nomination["capture_ref"]
            or usage.get("capture_ref") != nomination["capture_ref"] or usage.get("receipt") != receipt["capture"]):
        raise ValueError("Scoped assignment requires actual settled shared-campaign lead capture and usage")
    # Verify manifests privately, without reading or forwarding the transcript.
    methods.artifact_path(nomination["capture_ref"]["artifact_id"])
    return assignment


SCIENTIFIC_LIMITS = {
    "numerical_outputs": "existing_harness_verified_only",
    "method_selection": "parent_comparison_evidence_required",
    "causality": "association_is_not_causation",
    "approval": "no_execution_or_scientific_approval",
}
BUNDLE_KEYS = {"format", "parent_session_id", "stage", "plan_hash", "binding_digest", "policy_digest",
               "packet_refs", "result_refs", "verified_evidence_refs", "children", "uncertainty", "scientific_limits"}


def reconcile_investigations(plan, results, *, packets, verified_evidence):
    """Assemble evidence links; never vote, average confidence, or mutate a plan.

    Packet/result entries are coordinator wrappers ``{ref, value}``. The actual
    capture verifier runs again before attachment and whenever projected later.
    Missing results are visible incomplete children, never fabricated success.
    """
    from .rna_investigation_contract import validate_investigation_packet, validate_investigation_result
    if type(packets) is not list or not 1 <= len(packets) <= 2 or type(results) is not list or len(results) > 2:
        raise ValueError("Reconcile one or two bounded independent investigations")
    result_by_id = {}
    for row in results:
        if type(row) is not dict or not {"ref", "value"} <= set(row):
            raise ValueError("Reconciliation requires retained result references")
        identity = row["value"]["investigation_id"]
        if identity in result_by_id:
            raise ValueError("Conflicting duplicate independent result")
        result_by_id[identity] = row
    children, uncertainty, packet_refs, result_refs = [], [], [], []
    stage = policy_digest = None
    for row in sorted(packets, key=lambda item: item["value"]["investigation_id"]):
        packet = validate_investigation_packet(row["value"])
        _ref(row["ref"])
        if (packet["parent_session_id"] != plan["session_id"] or packet["bindings"]["plan_hash"] != digest(plan)
                or packet["bindings"]["binding_digest"] != planning_binding(plan)):
            raise ValueError("Reconciliation packet plan or dataset binding is stale")
        stage = stage or packet["stage"]
        policy_digest = policy_digest or packet["bindings"]["policy_digest"]
        if stage != packet["stage"] or policy_digest != packet["bindings"]["policy_digest"]:
            raise ValueError("Independent packet stages or policies disagree")
        identity = packet["investigation_id"]
        found = result_by_id.pop(identity, None)
        result = validate_investigation_result(found["value"], packet=packet) if found else None
        result_ref = found["ref"] if found else None
        if result_ref is not None:
            _ref(result_ref)
            result_refs.append(result_ref)
        dispatch = row.get("dispatch_ref")
        if dispatch is not None:
            _ref(dispatch)
        error = row.get("error")
        if error is not None and (type(error) is not str or len(error) > 500):
            raise ValueError("Use bounded child failure details")
        children.append({"investigation_id": identity, "packet_ref": row["ref"], "result_ref": result_ref,
                         "dispatch_ref": dispatch, "status": result["status"] if result else "incomplete", "error": error})
        packet_refs.append(row["ref"])
        if result:
            uncertainty.extend(result["uncertainty"])
        else:
            uncertainty.append("No validated child result is available for " + identity)
    if result_by_id or len({row["investigation_id"] for row in children}) != len(children):
        raise ValueError("Independent results differ from the exact allocated child identities")
    _refs(verified_evidence, 128)
    bundle = {"format": BUNDLE_FORMAT, "parent_session_id": plan["session_id"], "stage": stage,
              "plan_hash": digest(plan), "binding_digest": planning_binding(plan), "policy_digest": policy_digest,
              "packet_refs": packet_refs, "result_refs": result_refs, "verified_evidence_refs": deepcopy(verified_evidence),
              "children": children, "uncertainty": uncertainty, "scientific_limits": deepcopy(SCIENTIFIC_LIMITS)}
    if len(canonical_json(bundle)) > MAX_PROJECTION_BYTES:
        raise ValueError("Reconciliation bundle exceeds its bounded byte budget")
    return bundle


def verify_investigation_bundle(methods, bundle_ref, *, plan, verify_child):
    """Verify retained children and captures against the bundle's exact snapshot."""
    if not callable(verify_child):
        raise ValueError("A trusted protected backend child verifier is required")
    bundle = read_investigation_json(methods, bundle_ref, max_bytes=MAX_PROJECTION_BYTES)
    if (type(bundle) is not dict or set(bundle) != BUNDLE_KEYS or bundle["format"] != BUNDLE_FORMAT
            or bundle["parent_session_id"] != plan["session_id"] or bundle["stage"] not in AXES
            or bundle["plan_hash"] != digest(plan) or bundle["binding_digest"] != planning_binding(plan)
            or bundle["scientific_limits"] != SCIENTIFIC_LIMITS):
        raise ValueError("Investigation bundle plan, stage, or scientific scope binding is stale")
    _sha(bundle["policy_digest"])
    for key in ("packet_refs", "result_refs", "verified_evidence_refs"):
        _refs(bundle[key], 2 if key != "verified_evidence_refs" else 128)
        for item in bundle[key]:
            # The standard resolver verifies bytes without loading matrices.
            methods.artifact_path(item["artifact_id"])
            manifest = methods.get("artifact", item["artifact_id"])
            if {key: manifest[key] for key in item} != item:
                raise ValueError("Bundle evidence reference changed")
    children = bundle["children"]
    if (type(children) is not list or not 1 <= len(children) <= 2
            or len({row["investigation_id"] for row in children}) != len(children)
            or bundle["packet_refs"] != [row["packet_ref"] for row in children]
            or bundle["result_refs"] != [row["result_ref"] for row in children if row["result_ref"] is not None]):
        raise ValueError("Investigation bundle child allocation differs from its references")
    from .rna_investigation_contract import validate_investigation_packet, validate_investigation_result
    for row in children:
        if (type(row) is not dict or set(row) != {"investigation_id", "packet_ref", "result_ref", "dispatch_ref", "status", "error"}
                or row["status"] not in {"completed", "abstained", "failed", "incomplete"}
                or row["error"] is not None and (type(row["error"]) is not str or len(row["error"]) > 500)):
            raise ValueError("Invalid bounded independent child evidence record")
        packet = validate_investigation_packet(read_investigation_json(methods, row["packet_ref"], max_bytes=48_000))
        if (packet["investigation_id"] != row["investigation_id"] or packet["parent_session_id"] != plan["session_id"]
                or packet["stage"] != bundle["stage"] or packet["bindings"]["plan_hash"] != bundle["plan_hash"]
                or packet["bindings"]["binding_digest"] != bundle["binding_digest"]
                or packet["bindings"]["policy_digest"] != bundle["policy_digest"]):
            raise ValueError("Child packet binding differs from retained parent evidence")
        from .rna_investigation_projection import verify_packet_projection
        # A compact artifact's hash cannot establish fitted gene membership or
        # statistics. Derive them again from the actual receipt-bound family.
        verify_packet_projection(methods, packet, plan)
        verify_investigation_assignment(methods, packet, plan)
        if row["result_ref"] is None:
            if row["status"] not in {"failed", "incomplete"} or row["dispatch_ref"] is None:
                raise ValueError("A missing child cannot be represented as successful evidence")
            completion = verify_child(row["packet_ref"], None, dispatch_ref=row["dispatch_ref"])
            if completion.get("status") not in {"failed", "incomplete", "uncertain", "cancelled"}:
                raise ValueError("Missing child evidence is inconsistent with retained capture")
        else:
            verified = verify_child(row["packet_ref"], row["result_ref"])
            result = validate_investigation_result(read_investigation_json(methods, row["result_ref"], max_bytes=24_000), packet=packet)
            if verified != result or result["status"] != row["status"]:
                raise ValueError("Child capture differs from the retained independent result")
    if (type(bundle["uncertainty"]) is not list or len(bundle["uncertainty"]) > 64
            or any(type(item) is not str or len(item) > 1000 for item in bundle["uncertainty"])):
        raise ValueError("Use bounded retained uncertainty statements")
    return bundle


def investigation_evidence_projection(methods, plan, *, verify_child):
    """Return a bounded verified view for an existing protected parent call."""
    links = plan.get("investigation_evidence", [])
    if type(links) is not list or len(links) > 1:
        raise ValueError("One frozen investigation fork is supported per planning cycle")
    output = []
    for link in links:
        bundle_ref = link["bundle_ref"]
        before = read_investigation_json(methods, bundle_ref, max_bytes=MAX_PROJECTION_BYTES)
        snapshot = methods.get("method_plan_snapshot", before["plan_hash"])
        if planning_binding(snapshot) != planning_binding(plan):
            raise ValueError("Investigation evidence dataset, objective, or requirements changed")
        bundle = verify_investigation_bundle(methods, bundle_ref, plan=snapshot, verify_child=verify_child)
        children = []
        for row in bundle["children"]:
            projected = {key: deepcopy(row[key]) for key in ("investigation_id", "packet_ref", "result_ref", "status", "error")}
            if row["result_ref"] is not None:
                result = read_investigation_json(methods, row["result_ref"], max_bytes=24_000)
                for key in ("data_assessment", "requirements_assessment", "proposals", "assumptions", "missing_information",
                            "evidence_claims", "uncertainty", "capture_ref", "usage_ref"):
                    projected[key] = deepcopy(result[key])
            children.append(projected)
        output.append({"bundle_ref": bundle_ref, "stage": bundle["stage"], "children": children,
                       "uncertainty": bundle["uncertainty"], "scientific_limits": bundle["scientific_limits"]})
    if len(canonical_json(output)) > MAX_PROJECTION_BYTES:
        raise ValueError("Parent evidence projection exceeds its byte budget; provide an admissible compact view")
    return output
