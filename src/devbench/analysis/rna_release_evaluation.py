"""Closed v2.4 engineering accounting, without campaign or scientific admission.

This is separate from the historical six-arm and three-arm evaluators. Inputs
are explicit post-seal records; this module neither obtains reference answers
nor dispatches participants. Hashes bind bytes, not independent approval.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import math
import os
from pathlib import Path
import statistics
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from devbench._layout import EVALUATION_PROTOCOL, resolve_analysis_asset
from devbench.engine.workspace import read_bounded_file
from devbench.study.contracts import canonical_json_bytes
from devbench.study.evidence import decode_json


PROTOCOL_ID = "devbench.rna-v24-evaluation-protocol/1"
FORMAT = "devbench.rna-release-evaluation/1"
LEDGER_FORMAT = "devbench.rna-release-ledger/1"
PACKAGE_FORMAT = "devbench.rna-release-score-package/1"
PARENT_SHA256 = "cd821694a23fc25dd13766af72cdca18a07fb4c63bb0eb350cc672531ad3e313"
REGIMES = ("B-I", "B-P", "S-I", "S-P", "P-P")
ARMS = ("A0", "Direct-R", "B", "C", "D", "D-late")
OWNER_ROLES = ("statistical", "domain", "custodian", "runtime", "release")
RESOURCE_KEYS = ("tokens", "calls", "queries", "numerical_jobs", "retries", "reviews",
                 "reconciliation_calls", "terminal_reporting_calls", "cpu_seconds", "gpu_seconds",
                 "memory_bytes", "storage_bytes", "transfer_bytes", "wall_seconds", "spend_usd")
MAX_RECORD_BYTES = 8 * 1024**2
MAX_PACKAGE_BYTES = 32 * 1024**2
MAX_ROWS = 4096
Text = Annotated[str, Field(min_length=1, max_length=512)]
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Nonnegative = Annotated[float, Field(ge=0)]
Probability = Annotated[float, Field(gt=0, lt=1)]
Names = Annotated[list[Text], Field(max_length=MAX_ROWS)]
ArmName = Literal["A0", "Direct-R", "B", "C", "D", "D-late"]
Regime = Literal["B-I", "B-P", "S-I", "S-P", "P-P"]
Ratification = Literal["unratified", "ratified"]


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Identity(_Record):
    name: Text
    sha256: Hash


class Usage(_Record):
    tokens: Nonnegative | None
    calls: Nonnegative | None
    queries: Nonnegative | None
    numerical_jobs: Nonnegative | None
    retries: Nonnegative | None
    reviews: Nonnegative | None
    reconciliation_calls: Nonnegative | None
    terminal_reporting_calls: Nonnegative | None
    cpu_seconds: Nonnegative | None
    gpu_seconds: Nonnegative | None
    memory_bytes: Nonnegative | None
    storage_bytes: Nonnegative | None
    transfer_bytes: Nonnegative | None
    wall_seconds: Nonnegative | None
    spend_usd: Nonnegative | None


class Budget(_Record):
    identity: Identity | None
    limits: Usage
    reserves: Usage
    enforcement: Literal["enforced", "observed_only", "unsupported", "unknown"]


class Bindings(_Record):
    model: Identity | None
    runtime: Identity | None
    worker: Identity | None
    prompt: Identity | None
    tools: Identity | None
    renderer: Identity | None
    numerics: Identity | None


class Owner(_Record):
    role: Literal["statistical", "domain", "custodian", "runtime", "release"]
    identity: Identity | None
    independent: bool | None
    approval: Identity | None
    conflicts: Names
    access_scope: Identity | None
    resources: Identity | None
    ratification: Ratification


class Grant(_Record):
    identity: Identity | None
    workload: Literal["engineering", "scientific", "model", "remote"]
    budget: Budget
    ratification: Ratification


class Arm(_Record):
    arm_id: ArmName
    implementation: Identity | None
    entrypoint: Text | None
    bindings: Bindings
    total_budget: Budget
    clarification_policy: Identity | None
    intervention_policy: Identity | None
    evidence_policy: Identity | None
    invocation_receipt: Identity | None
    executable: bool | None


class Study(_Record):
    study_id: Text
    underlying_study_id: Text
    biological_source_ids: Annotated[list[Text], Field(min_length=1, max_length=1024)]
    variant_parent_ids: Names
    generator_identity: Identity | None
    partition: Literal["development", "pilot", "locked_qualification"]
    regime: Regime
    stratum: Text
    measurements: Identity | None
    requirement_source: Identity | None
    independent: bool | None
    hidden_reference_access_by_participant: bool


class Endpoint(_Record):
    endpoint_id: Text
    kind: Literal["fdp", "global_null_any_discovery", "critical_integrity", "numerical_agreement",
                  "review_detection", "initial_false_hold", "resolved_hold", "harmful_closure",
                  "terminal_false_block", "useful_completion", "cost_burden", "paired_benefit", "x9"]
    denominator: Literal["independent_studies", "all_assigned_studies", "eligible_corrupted_packets",
                         "assessed_valid_packets", "review_touchpoints", "answerable_assigned_studies",
                         "numerical_quantities", "useful_answers", "initial_false_holds",
                         "integrity_cases", "integrity_opportunities"]
    partition: Literal["development", "pilot", "locked_qualification"]
    regime: Regime
    reporting_scope: Text
    arm_ids: Annotated[list[ArmName], Field(min_length=1, max_length=6)]
    family_ids: Names
    stratum: Text
    bound_method: Literal["empirical_bernstein", "exact_binomial"] | None
    confidence_allocation_id: Text | None
    required_quantities: Names
    ratification: Ratification


class Allocation(_Record):
    allocation_id: Text
    endpoint_id: Text
    family_id: Text | None
    stratum: Text
    gate_id: Text
    delta: Probability


class Confidence(_Record):
    global_delta: Probability | None
    allocations: Annotated[list[Allocation], Field(max_length=MAX_ROWS)]
    ratification: Ratification


class Sampling(_Record):
    method: Identity | None
    power: Identity | None
    stopping: Identity | None
    independent_studies: bool | None
    fixed_n: Annotated[int, Field(ge=2, le=100_000)] | None
    ratification: Ratification


class Freeze(_Record):
    frozen_at: Text | None
    first_result_at: Text | None
    predecessor_sha256: Hash | None
    changes_after_results: bool


class Tolerance(_Record):
    tolerance_id: Text
    quantity: Text
    scale: Literal["linear", "log"] | None
    absolute: Nonnegative | None
    relative: Nonnegative | None
    near_zero: Literal["absolute_only", "unavailable"] | None
    boundary: Literal["compare", "unavailable"] | None
    nonfinite: Literal["unavailable"] | None
    missing: Literal["unavailable"] | None
    regimes: Annotated[list[Regime], Field(min_length=1, max_length=5)]
    ratification: Ratification


class Gate(_Record):
    gate_id: Text
    endpoint_ids: Names
    threshold: Nonnegative | None
    direction: Literal["minimum", "maximum"] | None
    owner_role: Literal["statistical", "domain", "custodian", "runtime", "release"]
    evidence: Identity | None
    ratification: Ratification


class X9Design(_Record):
    status: Literal["unratified_engineering_proposal"]
    provenance: Literal["repository_protocol_not_user_prespecified"]
    factors: list[Literal["retrieval_enabled", "common_packet"]]
    packet: Identity | None
    packet_frozen_at: Text | None
    cost_attribution: Literal["charged_to_total_budget"] | None


class FamilyAllocation(_Record):
    family_id: Text
    population_id: Text
    contrast_id: Text
    weight: Annotated[float, Field(ge=0, le=1)] | None
    required_primary: bool


class Envelope(_Record):
    regime: Regime
    assay: Text | None
    representation: Identity | None
    biological_unit_definition: Identity | None
    label_rule: Identity | None
    coverage_rule: Identity | None
    design_rule: Identity | None
    capacity: Identity | None
    ratification: Ratification


class Latency(_Record):
    method: Literal["nearest_rank"] | None
    unit: Literal["assigned_study_wall_interval"] | None
    ratification: Ratification


Aggregation = Literal["sum", "maximum"] | None


class ResourceAggregation(_Record):
    tokens: Aggregation
    calls: Aggregation
    queries: Aggregation
    numerical_jobs: Aggregation
    retries: Aggregation
    reviews: Aggregation
    reconciliation_calls: Aggregation
    terminal_reporting_calls: Aggregation
    cpu_seconds: Aggregation
    gpu_seconds: Aggregation
    memory_bytes: Aggregation
    storage_bytes: Aggregation
    transfer_bytes: Aggregation
    wall_seconds: Aggregation
    spend_usd: Aggregation


class ReleaseProtocol(_Record):
    format: Literal["devbench.rna-release-evaluation/1"]
    protocol_id: Literal["devbench.rna-v24-evaluation-protocol/1"]
    parent_spec_sha256: Literal["cd821694a23fc25dd13766af72cdca18a07fb4c63bb0eb350cc672531ad3e313"]
    provenance: Literal["repository_protocol_not_user_prespecified"]
    candidate_source: Identity | None
    evaluator_source: Identity | None
    protocol_source: Identity | None
    required_regimes: list[Regime]
    envelopes: Annotated[list[Envelope], Field(max_length=5)]
    testing_alpha: Probability | None
    family_allocations: Annotated[list[FamilyAllocation], Field(max_length=MAX_ROWS)]
    resource_aggregation: ResourceAggregation
    latency: Latency
    owners: Annotated[list[Owner], Field(max_length=5)]
    grants: Annotated[list[Grant], Field(max_length=32)]
    arms: Annotated[list[Arm], Field(max_length=6)]
    studies: Annotated[list[Study], Field(max_length=MAX_ROWS)]
    endpoints: Annotated[list[Endpoint], Field(max_length=MAX_ROWS)]
    sampling: Sampling
    confidence: Confidence
    freeze: Freeze
    tolerances: Annotated[list[Tolerance], Field(max_length=256)]
    gates: Annotated[list[Gate], Field(max_length=256)]
    intervention_policy: Identity | None
    utility_floor: Nonnegative | None
    x9: X9Design


class Assignment(_Record):
    assignment_id: Text
    study_id: Text
    arm_id: ArmName
    endpoint_ids: Names
    expected_attempt_ids: Annotated[list[Text], Field(min_length=1, max_length=MAX_ROWS)]


class Hypothesis(_Record):
    hypothesis_id: Text
    family_id: Text
    contrast_id: Text
    population_id: Text
    reporting_scope: Text
    exposed: bool
    retained: bool
    withdrawn: bool
    false_discovery: bool | None
    adjudication: Identity | None


class Intervention(_Record):
    intervention_id: Text
    actor: Identity
    policy: Identity | None
    action: Text
    scope: Text
    usage: Usage


class X9Attempt(_Record):
    retrieval_enabled: bool
    common_packet: bool
    packet: Identity | None
    packet_received_before_result: bool | None
    packet_cost: Usage
    actual_bindings: Bindings
    actual_total_budget: Budget
    actual_clarification: Identity | None
    actual_intervention: Identity | None
    actual_numerical_output: Identity | None
    capture_correct: bool | None


class Attempt(_Record):
    attempt_id: Text
    assignment_id: Text
    retry_of: Text | None
    status: Literal["completed", "failed", "partial", "timeout", "cancelled", "blocked", "uncertain", "unlaunched"]
    exposures_known: bool
    hypotheses: Annotated[list[Hypothesis], Field(max_length=100_000)]
    usage: Usage
    invocation: Identity | None
    interventions: Annotated[list[Intervention], Field(max_length=1024)]
    started_at: Text | None
    ended_at: Text | None
    x9: X9Attempt | None


class Outcome(_Record):
    assignment_id: Text
    terminal_attempt_id: Text
    useful: bool | None
    answerable: bool | None
    terminal_false_block: bool | None
    harmful_closure: bool | None
    adjudication: Identity | None
    independent: bool | None


class ReviewPacket(_Record):
    packet_id: Text
    assignment_id: Text
    attempt_id: Text
    touchpoint_id: Text
    valid: bool | None
    consequential_error: bool | None
    detected: bool | None
    initial_hold: bool | None
    initial_hold_erroneous: bool | None
    resolved: bool | None
    harmful_closure: bool | None
    adjudication: Identity | None
    independent: bool | None


class NumericalRecord(_Record):
    record_id: Text
    assignment_id: Text
    quantity: Text
    observed: float | None
    expected: float | None
    tolerance_id: Text
    observed_status: Literal["finite", "missing", "nonfinite", "failed"]
    reference_status: Literal["finite", "missing", "nonfinite", "failed"]
    reference_binding: Identity | None


class FaultRecord(_Record):
    record_id: Text
    assignment_id: Text
    fault_id: Literal["F1", "F2", "F3", "F4", "F5", "F6", "F7"]
    variant_id: Text
    control: Literal["clean", "adversarial"]
    independent_variant: bool | None
    expected_behavior: Text
    observed_behavior: Text | None
    entrypoint: Identity | None
    invocation: Identity | None
    resource_receipt: Identity | None
    interventions: list[Identity]


class IntegrityRecord(_Record):
    record_id: Text
    assignment_id: Text
    case_id: Text
    opportunity_id: Text
    critical_violation: bool | None
    accepted: bool | None
    adjudication: Identity | None
    independent: bool | None


class ReleaseLedger(_Record):
    format: Literal["devbench.rna-release-ledger/1"]
    protocol_sha256: Hash
    assignments: Annotated[list[Assignment], Field(max_length=MAX_ROWS)]
    attempts: Annotated[list[Attempt], Field(max_length=MAX_ROWS)]
    outcomes: Annotated[list[Outcome], Field(max_length=MAX_ROWS)]
    review_packets: Annotated[list[ReviewPacket], Field(max_length=MAX_ROWS)]
    numerical_records: Annotated[list[NumericalRecord], Field(max_length=100_000)]
    fault_records: Annotated[list[FaultRecord], Field(max_length=MAX_ROWS)]
    integrity_records: Annotated[list[IntegrityRecord], Field(max_length=MAX_ROWS)]


def record_digest(value):
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _checked(model, value):
    raw = canonical_json_bytes(value)
    if len(raw) > MAX_RECORD_BYTES:
        raise ValueError("release_record_byte_bound")
    return model.model_validate(value).model_dump(mode="json")


def unratified_protocol():
    """Repository-protocol scaffold, explicitly without invented campaign facts."""
    def budget():
        return {"identity": None, "limits": dict.fromkeys(RESOURCE_KEYS), "reserves": dict.fromkeys(RESOURCE_KEYS), "enforcement": "unknown"}
    return {"format": FORMAT, "protocol_id": PROTOCOL_ID, "parent_spec_sha256": PARENT_SHA256,
        "provenance": "repository_protocol_not_user_prespecified", "candidate_source": None,
        "evaluator_source": None, "protocol_source": None, "required_regimes": list(REGIMES),
        "envelopes": [{"regime": regime, "assay": None, "representation": None, "biological_unit_definition": None,
            "label_rule": None, "coverage_rule": None, "design_rule": None, "capacity": None,
            "ratification": "unratified"} for regime in REGIMES],
        "testing_alpha": None, "family_allocations": [], "resource_aggregation": dict.fromkeys(RESOURCE_KEYS),
        "latency": {"method": None, "unit": None, "ratification": "unratified"},
        "owners": [{"role": role, "identity": None, "independent": None, "approval": None,
                    "conflicts": [], "access_scope": None, "resources": None, "ratification": "unratified"} for role in OWNER_ROLES],
        "grants": [], "arms": [{"arm_id": arm, "implementation": None, "entrypoint": None,
            "bindings": dict.fromkeys(Bindings.model_fields), "total_budget": budget(),
            "clarification_policy": None, "intervention_policy": None, "evidence_policy": None,
            "invocation_receipt": None, "executable": None} for arm in ARMS],
        "studies": [], "endpoints": [], "sampling": {"method": None, "power": None, "stopping": None,
            "independent_studies": None, "fixed_n": None, "ratification": "unratified"},
        "confidence": {"global_delta": None, "allocations": [], "ratification": "unratified"},
        "freeze": {"frozen_at": None, "first_result_at": None, "predecessor_sha256": None,
                   "changes_after_results": False}, "tolerances": [], "gates": [],
        "intervention_policy": None, "utility_floor": None,
        "x9": {"status": "unratified_engineering_proposal", "provenance": "repository_protocol_not_user_prespecified",
               "factors": ["retrieval_enabled", "common_packet"], "packet": None,
               "packet_frozen_at": None, "cost_attribution": None}}


def _index(rows, key):
    result = {}
    for row in rows:
        if row[key] in result:
            raise ValueError("duplicate_release_identity:" + key)
        result[row[key]] = row
    return result


def _unique(values, label):
    if len(values) != len(set(values)):
        raise ValueError("duplicate_release_values:" + label)


def _instant(value):
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("invalid_release_timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError("release_timestamp_requires_timezone")
    return parsed


def _clusters(studies):
    by_id = _index(studies, "study_id")
    visited, active = set(), set()
    def visit(key):
        if key in active:
            raise ValueError("variant_lineage_cycle")
        if key not in by_id:
            raise ValueError("missing_variant_lineage")
        if key in visited:
            return
        active.add(key)
        for ancestor in by_id[key]["variant_parent_ids"]:
            visit(ancestor)
        active.remove(key)
        visited.add(key)
    for key in by_id:
        visit(key)
    parent = {key: key for key in by_id}
    def root(key):
        while parent[key] != key:
            key = parent[key]
        return key
    def union(a, b):
        a, b = root(a), root(b)
        parent[max(a, b)] = min(a, b)
    underlying, biological = {}, {}
    for row in studies:
        key = row["study_id"]
        _unique(row["biological_source_ids"], "biological_source_ids")
        _unique(row["variant_parent_ids"], "variant_parent_ids")
        if row["hidden_reference_access_by_participant"]:
            raise ValueError("participant_hidden_reference_access")
        origin = row["underlying_study_id"]
        if origin in underlying:
            union(key, underlying[origin])
        underlying[origin] = key
        for source in row["biological_source_ids"]:
            if source in biological:
                union(key, biological[source])
            biological[source] = key
        for variant in row["variant_parent_ids"]:
            if variant not in by_id or variant == key:
                raise ValueError("missing_or_self_variant_lineage")
            union(key, variant)
    groups = {}
    for key, row in by_id.items():
        groups.setdefault(root(key), []).append(row)
    for rows in groups.values():
        if len({row["partition"] for row in rows}) != 1:
            raise ValueError("study_partition_leakage")
    return {key: root(key) for key in by_id}, groups


def _qualification():
    return dict.fromkeys(("scientific", "numerical", "adaptive_X8", "literature_X9", "R0", "R1", "remote", "clinical"), False)


def _missing(value, prefix, blockers):
    if value is None:
        blockers.add("unassigned:" + prefix)
    elif isinstance(value, str) and value in ("unratified", "unknown", "unsupported"):
        blockers.add("unratified_or_unsupported:" + prefix)
    elif isinstance(value, dict):
        for key, item in value.items():
            _missing(item, prefix + "." + key, blockers)
    elif isinstance(value, list):
        if not value:
            blockers.add("unassigned_inventory:" + prefix)
        for index, item in enumerate(value):
            _missing(item, prefix + "." + str(index), blockers)


def validate_release_protocol(value):
    p = _checked(ReleaseProtocol, value)
    evaluator = _implementation()
    if p["evaluator_source"] is not None and p["evaluator_source"] != evaluator:
        raise ValueError("declared_evaluator_source_conflicts_with_actual_evaluator")
    if p["protocol_source"] is not None:
        source = resolve_analysis_asset(EVALUATION_PROTOCOL)
        if p["protocol_source"]["sha256"] != hashlib.sha256(read_bounded_file(source, MAX_RECORD_BYTES)).hexdigest():
            raise ValueError("declared_repository_protocol_source_conflicts_with_protocol_identity")
    if p["required_regimes"] != list(REGIMES):
        raise ValueError("all_five_R1_regimes_required")
    if set(_index(p["envelopes"], "regime")) != set(REGIMES):
        raise ValueError("all_five_R1_envelopes_required")
    families = _index(p["family_allocations"], "family_id")
    family_pairs = [(family["population_id"], family["contrast_id"]) for family in families.values()]
    if len(family_pairs) != len(set(family_pairs)):
        raise ValueError("duplicate_frozen_population_contrast_family")
    weights = [f["weight"] for f in families.values() if f["weight"] is not None]
    if math.fsum(weights) > 1 + 1e-15 or any(f["required_primary"] and f["weight"] == 0 for f in families.values()):
        raise ValueError("invalid_frozen_family_allocation")
    for key, method in p["resource_aggregation"].items():
        if key not in {"memory_bytes", "storage_bytes"} and method == "maximum":
            raise ValueError("cumulative_resources_and_spend_cannot_discard_retries")
    arms, owners = _index(p["arms"], "arm_id"), _index(p["owners"], "role")
    if set(arms) != set(ARMS) or set(owners) != set(OWNER_ROLES):
        raise ValueError("complete_prospective_arms_and_owners_required")
    for owner in owners.values():
        if owner["conflicts"] and owner["independent"] is True:
            raise ValueError("independent_owner_conflict_declared")
    runtime = owners["runtime"]["identity"]
    if runtime is not None and any(owners[role]["identity"] == runtime and owners[role]["independent"] is True
                                   for role in ("statistical", "domain", "custodian")):
        raise ValueError("runtime_owner_cannot_self_certify_independent_evaluation")
    for budget in [a["total_budget"] for a in arms.values()] + [g["budget"] for g in p["grants"]]:
        _validate_budget(budget)
    if arms["D-late"]["entrypoint"] is not None and "sequential_second" in arms["D-late"]["entrypoint"]:
        raise ValueError("D_late_must_not_alias_historical_sequential_second")
    _clusters(p["studies"])
    endpoints = _index(p["endpoints"], "endpoint_id")
    gates = _index(p["gates"], "gate_id")
    _index(p["tolerances"], "tolerance_id")
    allocations = _index(p["confidence"]["allocations"], "allocation_id")
    if allocations and (p["confidence"]["global_delta"] is None or
            math.fsum(item["delta"] for item in allocations.values()) > p["confidence"]["global_delta"]):
        raise ValueError("confidence_allocation_overspend_or_missing_global_budget")
    scopes = set()
    for allocation in allocations.values():
        ep = endpoints.get(allocation["endpoint_id"])
        if ep is None or allocation["gate_id"] not in gates or allocation["stratum"] != ep["stratum"]:
            raise ValueError("confidence_allocation_scope_unbound")
        if allocation["endpoint_id"] not in gates[allocation["gate_id"]]["endpoint_ids"]:
            raise ValueError("confidence_allocation_endpoint_outside_declared_gate")
        if allocation["family_id"] is not None and allocation["family_id"] not in ep["family_ids"]:
            raise ValueError("confidence_family_unbound")
        scope = tuple(allocation[k] for k in ("endpoint_id", "family_id", "stratum", "gate_id"))
        if scope in scopes:
            raise ValueError("duplicate_confidence_claim")
        scopes.add(scope)
    for ep in endpoints.values():
        for field in ("arm_ids", "family_ids", "required_quantities"):
            _unique(ep[field], field)
        if ep["kind"] in {"fdp", "global_null_any_discovery"} and ep["denominator"] != "independent_studies":
            raise ValueError("pooled_discovery_or_gene_denominator_forbidden")
        denominators = {
            "critical_integrity": {"all_assigned_studies", "independent_studies", "integrity_cases", "integrity_opportunities"},
            "numerical_agreement": {"numerical_quantities"}, "review_detection": {"eligible_corrupted_packets"},
            "initial_false_hold": {"assessed_valid_packets"}, "resolved_hold": {"initial_false_holds"},
            "harmful_closure": {"review_touchpoints", "all_assigned_studies", "independent_studies"},
            "terminal_false_block": {"answerable_assigned_studies"}, "useful_completion": {"all_assigned_studies"},
            "cost_burden": {"all_assigned_studies", "useful_answers"},
            "paired_benefit": {"independent_studies"}, "x9": {"independent_studies"},
        }
        if ep["kind"] in denominators and ep["denominator"] not in denominators[ep["kind"]]:
            raise ValueError("endpoint_kind_and_denominator_conflict")
        if set(ep["family_ids"]) - set(families):
            raise ValueError("endpoint_family_inventory_unbound")
        if ep["bound_method"] is not None:
            if len(ep["arm_ids"]) != 1:
                raise ValueError("each_asserted_arm_bound_requires_separate_endpoint_and_confidence_allocation")
            wanted = "empirical_bernstein" if ep["kind"] == "fdp" else "exact_binomial" if ep["kind"] == "global_null_any_discovery" else None
            if ep["bound_method"] != wanted:
                raise ValueError("unsupported_endpoint_bound_method")
            allocation = allocations.get(ep["confidence_allocation_id"])
            if allocation is None or allocation["endpoint_id"] != ep["endpoint_id"]:
                raise ValueError("asserted_bound_requires_frozen_confidence_allocation")
    for gate in gates.values():
        if not gate["endpoint_ids"] or set(gate["endpoint_ids"]) - set(endpoints):
            raise ValueError("gate_endpoints_unbound")
    frozen, result = _instant(p["freeze"]["frozen_at"]), _instant(p["freeze"]["first_result_at"])
    if p["freeze"]["changes_after_results"] or frozen and result and frozen >= result:
        raise ValueError("post_result_protocol_change")
    packet_time = _instant(p["x9"]["packet_frozen_at"])
    if packet_time and result and packet_time >= result:
        raise ValueError("X9_packet_not_frozen_before_results")
    if p["x9"]["factors"] != ["retrieval_enabled", "common_packet"]:
        raise ValueError("X9_engineering_factor_identity_changed")
    blockers = {"scientific_gates_held_by_user", "record_validation_does_not_authenticate_independent_approval"}
    _missing(p, "protocol", blockers)
    for arm in arms.values():
        if arm["executable"] is not True:
            blockers.add("arm_not_executable:" + arm["arm_id"])
        elif arm["implementation"] is None or arm["entrypoint"] is None or arm["invocation_receipt"] is None:
            raise ValueError("executable_arm_missing_binding_or_receipt")
    for owner in owners.values():
        if owner["independent"] is not True:
            blockers.add("independent_owner_unassigned:" + owner["role"])
    return {"protocol_id": PROTOCOL_ID, "protocol_sha256": record_digest(value), "evaluator": evaluator, "admission": "blocked",
            "blockers": sorted(blockers), "qualification": _qualification(), "model_calls": 0, "numerical_jobs": 0}


def _validate_budget(budget):
    for key in RESOURCE_KEYS:
        limit, reserve = budget["limits"][key], budget["reserves"][key]
        if limit is not None and reserve is not None and reserve > limit:
            raise ValueError("resource_reserve_exceeds_total_budget")
    if budget["enforcement"] == "enforced" and (budget["identity"] is None or
            any(v is None for v in budget["limits"].values())):
        raise ValueError("enforced_budget_has_unknown_limits_or_identity")


def empirical_bernstein_upper(values, *, delta, independent, sampling_frozen, stopping_frozen):
    """Registered arithmetic only; calling this function grants no qualification."""
    if (independent is not True or sampling_frozen is not True or stopping_frozen is not True
            or type(delta) not in (int, float) or not 0 < delta < 1
            or not 2 <= len(values) <= 100_000
            or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in values)):
        raise ValueError("empirical_bernstein_requires_independent_frozen_study_draws_and_delta")
    mean, n = statistics.fmean(values), len(values)
    variance = math.fsum((value - mean) ** 2 for value in values) / (n - 1)
    logarithm = math.log(2 / delta)
    return min(1., mean + math.sqrt(2 * variance * logarithm / n) + 7 * logarithm / (3 * (n - 1)))


def exact_global_null_upper(successes, n, *, delta):
    """One-sided exact binomial upper limit for global-null any-discovery draws."""
    if (type(n) is not int or type(successes) is not int or not 0 <= successes <= n
            or not 1 <= n <= 100_000 or type(delta) not in (int, float) or not 0 < delta < 1):
        raise ValueError("invalid_global_null_binomial_endpoint")
    if successes == n:
        return 1.
    if successes == 0:
        return -math.expm1(math.log(delta) / n)
    coefficients = [math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
                    for i in range(successes + 1)]
    lower, upper = 0., 1.
    log_delta = math.log(delta)
    for _ in range(64):
        value = (lower + upper) / 2
        if value in (lower, upper):
            break
        logs = [coefficient + i * math.log(value) + (n - i) * math.log1p(-value)
                for i, coefficient in enumerate(coefficients)]
        maximum = max(logs)
        log_cdf = maximum + math.log(math.fsum(math.exp(v - maximum) for v in logs))
        if log_cdf > log_delta:
            lower = value
        else:
            upper = value
    return upper


def _hypothesis_key(row):
    return tuple(row[k] for k in ("reporting_scope", "family_id", "contrast_id", "population_id", "hypothesis_id"))


def _validate_ledger(p, value):
    ledger = _checked(ReleaseLedger, value)
    if ledger["protocol_sha256"] != record_digest(p):
        raise ValueError("frozen_protocol_digest_changed")
    studies, endpoints = _index(p["studies"], "study_id"), _index(p["endpoints"], "endpoint_id")
    families = _index(p["family_allocations"], "family_id")
    assignments = _index(ledger["assignments"], "assignment_id")
    attempts, outcomes = _index(ledger["attempts"], "attempt_id"), _index(ledger["outcomes"], "assignment_id")
    expected, per_study = set(), set()
    for assignment in assignments.values():
        if assignment["study_id"] not in studies or assignment["arm_id"] not in ARMS:
            raise ValueError("assignment_study_or_arm_unbound")
        _unique(assignment["endpoint_ids"], "assignment_endpoints")
        if not assignment["endpoint_ids"] or set(assignment["endpoint_ids"]) - set(endpoints):
            raise ValueError("assignment_endpoint_unbound")
        for ep_id in assignment["endpoint_ids"]:
            ep = endpoints[ep_id]
            study = studies[assignment["study_id"]]
            if assignment["arm_id"] not in ep["arm_ids"] or any(study[key] != ep[key] for key in ("stratum", "partition", "regime")):
                raise ValueError("assignment_endpoint_scope_changed")
        expected_endpoints = {ep_id for ep_id, ep in endpoints.items()
            if assignment["arm_id"] in ep["arm_ids"] and all(study[key] == ep[key] for key in ("stratum", "partition", "regime"))}
        if set(assignment["endpoint_ids"]) != expected_endpoints:
            raise ValueError("complete_assigned_endpoint_inventory_required")
        pair = (assignment["study_id"], assignment["arm_id"])
        if pair in per_study:
            raise ValueError("repeated_seed_must_be_attempt_not_new_assignment")
        per_study.add(pair)
        for tid in assignment["expected_attempt_ids"]:
            if tid in expected:
                raise ValueError("duplicate_expected_attempt")
            expected.add(tid)
            if tid not in attempts or attempts[tid]["assignment_id"] != assignment["assignment_id"]:
                raise ValueError("assigned_attempt_missing_or_moved")
    if set(attempts) != expected or set(outcomes) != set(assignments):
        raise ValueError("complete_assignment_attempt_outcome_denominator_required")
    if set(studies) != {a["study_id"] for a in assignments.values()}:
        raise ValueError("assigned_study_denominator_missing")
    expected_pairs = {(study["study_id"], arm) for study in studies.values() for ep in endpoints.values()
                      if all(study[key] == ep[key] for key in ("stratum", "partition", "regime")) for arm in ep["arm_ids"]}
    if per_study != expected_pairs:
        raise ValueError("complete_prospective_arm_assignment_denominator_required")
    for attempt in attempts.values():
        prior = attempt["retry_of"]
        if prior is not None and (prior not in attempts or prior == attempt["attempt_id"]
                or attempts[prior]["assignment_id"] != attempt["assignment_id"]):
            raise ValueError("retry_origin_unbound")
        seen, cursor = set(), attempt
        while cursor["retry_of"] is not None:
            if cursor["attempt_id"] in seen:
                raise ValueError("retry_cycle")
            seen.add(cursor["attempt_id"])
            cursor = attempts[cursor["retry_of"]]
        keys = [_hypothesis_key(h) for h in attempt["hypotheses"]]
        _unique(keys, "hypotheses")
        for h in attempt["hypotheses"]:
            family = families.get(h["family_id"])
            if family is None or any(h[key] != family[key] for key in ("population_id", "contrast_id")):
                raise ValueError("hypothesis_frozen_family_binding_mismatch:" + h["family_id"])
            if h["withdrawn"] and not h["exposed"] or h["withdrawn"] and h["retained"]:
                raise ValueError("contradictory_hypothesis_exposure")
            if h["exposed"] or h["retained"]:
                a = assignments[attempt["assignment_id"]]
                if not any(h["family_id"] in endpoints[e]["family_ids"] and h["reporting_scope"] == endpoints[e]["reporting_scope"]
                           for e in a["endpoint_ids"]):
                    raise ValueError("exposed_hypothesis_outside_assigned_accounting_scope")
        start, end = _instant(attempt["started_at"]), _instant(attempt["ended_at"])
        if start and end and end < start:
            raise ValueError("attempt_time_reversal")
        _index(attempt["interventions"], "intervention_id")
        for key in RESOURCE_KEYS:
            usage = _total([i["usage"][key] for i in attempt["interventions"]])
            total = attempt["usage"][key]
            if usage is not None and total is not None and usage > total:
                raise ValueError("intervention_usage_not_in_total")
    for outcome in outcomes.values():
        terminal = attempts.get(outcome["terminal_attempt_id"])
        if terminal is None or terminal["assignment_id"] != outcome["assignment_id"]:
            raise ValueError("terminal_attempt_unbound")
        if outcome["useful"] is True and terminal["status"] != "completed":
            raise ValueError("failed_attempt_cannot_be_useful_completion")
        if outcome["terminal_false_block"] is True and outcome["answerable"] is not True:
            raise ValueError("false_block_requires_answerable_study")
    for field, key in (("review_packets", "packet_id"), ("numerical_records", "record_id"), ("fault_records", "record_id"),
                       ("integrity_records", "record_id")):
        _index(ledger[field], key)
        if any(row["assignment_id"] not in assignments for row in ledger[field]):
            raise ValueError("evaluation_row_assignment_unbound")
    for packet in ledger["review_packets"]:
        if packet["attempt_id"] not in attempts or attempts[packet["attempt_id"]]["assignment_id"] != packet["assignment_id"]:
            raise ValueError("review_attempt_unbound")
        if packet["initial_hold_erroneous"] is True and (packet["initial_hold"] is not True or packet["valid"] is not True):
            raise ValueError("erroneous_hold_requires_valid_held_packet")
        if packet["resolved"] is True and packet["initial_hold"] is not True:
            raise ValueError("resolved_hold_missing_initial_hold")
    return ledger, assignments, attempts, outcomes, studies


def _total(values):
    return None if any(value is None for value in values) else math.fsum(values)


def _rate(numerator, denominator, unknown=0):
    return {"numerator": numerator, "denominator": denominator, "unknown": unknown,
            "point": numerator / denominator if denominator and not unknown else None,
            "confidence_interval": None, "uncertainty": "unratified"}


def _judged_boolean(row, field):
    """Negative eligibility needs the same independent evidence as positive truth."""
    return row[field] if row["adjudication"] is not None and row["independent"] is True else None


def _eligibility_rate(rows, *, eligibility, outcome):
    """Classify every scoped unit before selecting confirmed eligible judgments."""
    eligible, ineligible, unknown_eligibility = [], 0, 0
    for row in rows:
        known = eligibility(row)
        if known is True:
            eligible.append(row)
        elif known is False:
            ineligible += 1
        else:
            unknown_eligibility += 1
    values = [outcome(row) for row in eligible]
    unknown_outcomes = sum(value is None for value in values)
    return {**_rate(sum(value is True for value in values), len(eligible), unknown_eligibility + unknown_outcomes),
            "scope_denominator": len(rows), "known_ineligible": ineligible,
            "unknown_eligibility": unknown_eligibility, "unknown_outcomes": unknown_outcomes}


def _group_judgment(rows, field):
    values = [_judged_boolean(row, field) for row in rows]
    if any(value is None for value in values):
        return None
    return any(values)


def _cluster_answerability(rows):
    """Consistent known views need no invented mixed-view scientific policy."""
    values = [_judged_boolean(row, "answerable") for row in rows]
    if any(value is None for value in values) or len(set(values)) > 1:
        return None
    return values[0]


def _fdp_scores(p, ledger, assignments, studies, clusters):
    scores = {}
    for ep in p["endpoints"]:
        if ep["kind"] not in {"fdp", "global_null_any_discovery"}:
            continue
        scores[ep["endpoint_id"]] = {}
        for arm in ep["arm_ids"]:
            selected = {key: a for key, a in assignments.items() if a["arm_id"] == arm and ep["endpoint_id"] in a["endpoint_ids"]}
            grouped = {clusters[a["study_id"]]: {"unknown": False, "hypotheses": {}} for a in selected.values()}
            for attempt in ledger["attempts"]:
                assignment = selected.get(attempt["assignment_id"])
                if assignment is None:
                    continue
                group = grouped[clusters[assignment["study_id"]]]
                group["unknown"] |= not attempt["exposures_known"]
                for h in attempt["hypotheses"]:
                    if h["reporting_scope"] != ep["reporting_scope"] or h["family_id"] not in ep["family_ids"] or not (h["exposed"] or h["retained"]):
                        continue
                    key = _hypothesis_key(h)
                    false = h["false_discovery"] if h["adjudication"] is not None else None
                    old = group["hypotheses"].get(key)
                    if old is not None and false is not None and old != false:
                        raise ValueError("conflicting_hypothesis_adjudication")
                    group["hypotheses"][key] = old if false is None else false
            rows = []
            for cluster, group in sorted(grouped.items()):
                unknown = group["unknown"] or any(v is None for v in group["hypotheses"].values())
                discoveries, false = len(group["hypotheses"]), sum(v is True for v in group["hypotheses"].values())
                rows.append({"study_cluster": cluster, "discoveries": discoveries, "false_discoveries": None if unknown else false,
                             "fdp": None if unknown else false / max(discoveries, 1), "exposures_known": not group["unknown"]})
            values = [r["fdp"] for r in rows]
            unknown = any(v is None for v in values)
            independent = all(studies[a["study_id"]]["independent"] is True for a in selected.values())
            bound = None
            confidence = next((x for x in p["confidence"]["allocations"] if x["allocation_id"] == ep["confidence_allocation_id"]), None)
            eligible = (rows and not unknown and independent and p["sampling"]["independent_studies"] is True
                and p["sampling"]["ratification"] == "ratified" and p["sampling"]["method"] is not None
                and p["sampling"]["stopping"] is not None and p["sampling"]["fixed_n"] == len(rows)
                and p["freeze"]["frozen_at"] is not None and p["freeze"]["first_result_at"] is not None
                and p["confidence"]["ratification"] == "ratified" and confidence is not None)
            if eligible and ep["bound_method"] == "empirical_bernstein" and len(rows) >= 2:
                bound = empirical_bernstein_upper(values, delta=confidence["delta"], independent=True, sampling_frozen=True, stopping_frozen=True)
            elif eligible and ep["bound_method"] == "exact_binomial":
                bound = exact_global_null_upper(sum(r["discoveries"] > 0 for r in rows), len(rows), delta=confidence["delta"])
            scores[ep["endpoint_id"]][arm] = {"studies": rows, "independent_n": len(rows) if independent else None,
                "study_clusters": len(rows), "mean_study_fdp": statistics.fmean(values) if values and not unknown else None,
                "upper_bound": bound, "uncertainty": "registered_arithmetic_only" if bound is not None else "unavailable_unratified",
                "scope": "descriptive_record_accounting_not_qualification"}
    return scores


def _review_scores(ledger):
    packets = ledger["review_packets"]
    touchpoints = {}
    for packet in packets:
        touchpoints.setdefault((packet["assignment_id"], packet["touchpoint_id"]), []).append(packet)
    def unknown(rows, field):
        return sum(p[field] is None or p["adjudication"] is None or p["independent"] is not True for p in rows)
    def erroneous_hold(packet):
        # No observed hold cannot be an erroneous hold. This action fact does
        # not establish the packet's scientific validity or error eligibility.
        return False if packet["initial_hold"] is False else _judged_boolean(packet, "initial_hold_erroneous")
    return {"review_detection": _eligibility_rate(packets,
            eligibility=lambda p: _judged_boolean(p, "consequential_error"), outcome=lambda p: _judged_boolean(p, "detected")),
        "initial_false_holds": _eligibility_rate(packets,
            eligibility=lambda p: _judged_boolean(p, "valid"), outcome=erroneous_hold),
        "resolved_false_holds": _eligibility_rate(packets,
            eligibility=erroneous_hold, outcome=lambda p: _judged_boolean(p, "resolved")),
        "harmful_closure_touchpoints": _rate(
            sum(any(p["harmful_closure"] is True and p["adjudication"] is not None and p["independent"] is True for p in rows)
                for rows in touchpoints.values()), len(touchpoints),
            sum(unknown(rows, "harmful_closure") > 0 for rows in touchpoints.values())),
        "packet_denominator": len(packets), "touchpoint_denominator": len(touchpoints),
        "unknown_validity_packets": sum(_judged_boolean(p, "valid") is None for p in packets),
        "independent_adjudication_complete": bool(packets) and all(p["adjudication"] is not None and p["independent"] is True for p in packets)}


def _numerical_scores(p, ledger, assignments, studies):
    tolerances = _index(p["tolerances"], "tolerance_id")
    rows = []
    observed_keys = set()
    for record in ledger["numerical_records"]:
        key = (record["assignment_id"], record["quantity"])
        if key in observed_keys:
            raise ValueError("duplicate_numerical_quantity")
        observed_keys.add(key)
        tolerance = tolerances.get(record["tolerance_id"])
        if tolerance is None or tolerance["quantity"] != record["quantity"]:
            raise ValueError("numerical_tolerance_unbound")
        result = None
        regime = studies[assignments[record["assignment_id"]]["study_id"]]["regime"]
        if regime not in tolerance["regimes"]:
            raise ValueError("numerical_tolerance_regime_mismatch")
        if (record["observed_status"] == record["reference_status"] == "finite" and record["observed"] is not None
                and record["expected"] is not None and record["reference_binding"] is not None
                and tolerance["ratification"] == "ratified" and all(tolerance[k] is not None for k in
                    ("scale", "absolute", "relative", "near_zero", "boundary", "nonfinite", "missing"))):
            raw_a, raw_b = record["observed"], record["expected"]
            raw_boundary = raw_a == 0 or raw_b == 0
            domain_available = tolerance["scale"] != "log" or raw_a > 0 and raw_b > 0
            if (domain_available and (not raw_boundary or tolerance["boundary"] == "compare")
                    and (not raw_boundary or tolerance["near_zero"] != "unavailable")):
                a, b = (math.log(raw_a), math.log(raw_b)) if tolerance["scale"] == "log" else (raw_a, raw_b)
                # No new near-zero cutoff is inferred. At the represented raw
                # zero boundary, the declared absolute-only rule has no relative
                # allowance. log(1) is zero in comparison space, not a boundary.
                allowance = tolerance["absolute"] + (0 if raw_boundary else tolerance["relative"] * abs(b))
                result = abs(a - b) <= allowance
        rows.append({"assignment_id": key[0], "quantity": key[1], "agreement": result})
    expected = {(a["assignment_id"], quantity) for a in assignments.values() for ep in p["endpoints"]
                if ep["kind"] == "numerical_agreement" and ep["endpoint_id"] in a["endpoint_ids"] for quantity in ep["required_quantities"]}
    rows.extend({"assignment_id": key[0], "quantity": key[1], "agreement": None} for key in sorted(expected - observed_keys))
    return {"quantities": rows, "expected_denominator": len(expected), "missing_expected": len(expected - observed_keys),
            "qualified": False, "scope": "engineering_comparison_only"}


def _x9_scores(p, ledger, assignments, outcomes, clusters):
    groups, missing, missing_actual = {}, set(), []
    declared = {ep["endpoint_id"] for ep in p["endpoints"] if ep["kind"] == "x9"}
    tagged = {a["assignment_id"] for a in ledger["attempts"] if a["x9"] is not None}
    relevant = {key: a for key, a in assignments.items() if key in tagged or declared.intersection(a["endpoint_ids"])}
    considered = [a for a in ledger["attempts"] if a["assignment_id"] in relevant]
    for aid, assignment in relevant.items():
        rows = [a for a in considered if a["assignment_id"] == aid]
        known = [a for a in rows if a["x9"] is not None]
        missing_actual.extend(a["attempt_id"] for a in rows if a["x9"] is None)
        cells = {(a["x9"]["retrieval_enabled"], a["x9"]["common_packet"]) for a in known}
        cluster = groups.setdefault(clusters[assignment["study_id"]], {})
        if len(cells) == 1:
            # A sibling's cell identifies the assigned comparison only; it does
            # not supply the missing retry's actual capabilities or packet cost.
            cluster.setdefault(next(iter(cells)), []).extend(rows)
        elif not cells:
            missing.add("assignment_has_no_actual_X9_identity")
        else:
            missing.add("assignment_changed_X9_cell")
        for attempt in known:
            x = attempt["x9"]
            _validate_budget(x["actual_total_budget"])
            for key in RESOURCE_KEYS:
                cost, total = x["packet_cost"][key], attempt["usage"][key]
                if cost is not None and total is not None and cost > total:
                    raise ValueError("X9_packet_cost_not_in_total_budget")
            if x["common_packet"] and x["packet"] != p["x9"]["packet"]:
                raise ValueError("X9_common_packet_identity_changed")
            if not x["common_packet"] and x["packet"] is not None:
                raise ValueError("X9_unassigned_packet_evidence")
    if missing_actual:
        missing.add("missing_actual_assignment_attempt_identity")
    for row in considered:
        if any(value is None for value in row["usage"].values()):
            missing.add("unknown_actual_usage_or_packet_cost")
    for cells in groups.values():
        if set(cells) != {(False, False), (False, True), (True, False), (True, True)}:
            missing.add("incomplete_common_packet_controls")
        rows = [row for values in cells.values() for row in values]
        known = [row for row in rows if row["x9"] is not None]
        signatures = {record_digest({key: row["x9"][key] for key in ("actual_bindings", "actual_total_budget", "actual_clarification", "actual_intervention", "actual_numerical_output")}) for row in known}
        if len(signatures) != 1:
            missing.add("unmatched_actual_capability_or_total_budget")
        for row in known:
            x = row["x9"]
            if any(value is None for value in row["usage"].values()) or any(value is None for value in x["packet_cost"].values()):
                missing.add("unknown_actual_usage_or_packet_cost")
            if (any(value is None for value in x["actual_bindings"].values()) or x["actual_clarification"] is None
                    or x["actual_intervention"] is None or x["actual_total_budget"]["identity"] is None
                    or any(v is None for v in x["actual_total_budget"]["limits"].values())
                    or any(v is None for v in x["actual_total_budget"]["reserves"].values())
                    or x["actual_total_budget"]["enforcement"] in {"unknown", "unsupported"}
                    or x["actual_numerical_output"] is None or row["invocation"] is None):
                missing.add("missing_actual_capability_identity")
            if x["common_packet"] and (x["packet_received_before_result"] is not True or x["packet"] is None):
                missing.add("packet_not_presupplied")
            outcome = outcomes[row["assignment_id"]]
            if outcome["adjudication"] is None or outcome["independent"] is not True or outcome["useful"] is None:
                missing.add("independent_usefulness_adjudication_unavailable")
        for cell in cells.values():
            budget = next(row["x9"]["actual_total_budget"] for row in cell if row["x9"] is not None)
            for key in RESOURCE_KEYS:
                values = [row["usage"][key] for row in cell]
                ceiling = budget["limits"][key]
                if None not in values and ceiling is not None:
                    used = max(values) if p["resource_aggregation"][key] == "maximum" else math.fsum(values)
                    if used > ceiling:
                        missing.add("actual_total_budget_exceeded_including_retries")
    if p["x9"]["packet_frozen_at"] is None or p["x9"]["cost_attribution"] is None:
        missing.add("packet_freeze_or_cost_attribution_unratified")
    return {"status": "not_assessed" if not groups or missing else "inconclusive", "study_clusters": len(groups),
            "blockers": sorted(missing), "design": "unratified_engineering_proposal",
            "attempts_considered": len(considered), "assignments_considered": len(relevant),
            "missing_actual_attempt_ids": sorted(missing_actual),
            "capture_correct_attempts": sum(a["x9"] is not None and a["x9"]["capture_correct"] is True for a in considered),
            "paired_benefit_interval": None, "uncertainty": "unratified", "improved_reliability": False,
            "capture_correctness_is_independent_usefulness": False}


def _assigned_summary(p, ledger, assignments, attempts, outcomes, studies, clusters):
    """Account one complete assignment view, including every retained attempt."""
    grouped = {}
    for a in assignments.values():
        grouped.setdefault((a["arm_id"], clusters[a["study_id"]]), []).append(outcomes[a["assignment_id"]])
    useful, blocking, closures, costs = {}, {}, {}, {}
    def usage_total(rows, key):
        values = [a["usage"][key] for a in rows]
        method = p["resource_aggregation"][key]
        if method is None or any(v is None for v in values):
            return None
        return max(values, default=0) if method == "maximum" else math.fsum(values)
    usage = {key: usage_total(attempts.values(), key) for key in RESOURCE_KEYS}
    for arm in sorted({a["arm_id"] for a in assignments.values()}):
        rows = [values for (a, _), values in grouped.items() if a == arm]
        # Repeated views never create new independent success opportunities.
        def validated(values):
            return all(r["useful"] is True and r["adjudication"] is not None and r["independent"] is True for r in values)
        success = sum(validated(values) for values in rows)
        unvalidated = sum(all(r["useful"] is True for r in values) and not validated(values) for values in rows)
        unknown = sum(any(r["useful"] is None or r["adjudication"] is None or r["independent"] is not True for r in values) for values in rows)
        useful[arm] = _rate(success, len(rows), unknown)
        useful[arm]["unvalidated_declared_useful_answers"] = unvalidated
        blocking[arm] = _eligibility_rate(rows,
            eligibility=_cluster_answerability,
            outcome=lambda values: _group_judgment(values, "terminal_false_block"))
        blocking[arm]["unknown_answerability_studies"] = blocking[arm]["unknown_eligibility"]
        reasons = set()
        for values in rows:
            answerability = [_judged_boolean(row, "answerable") for row in values]
            if None in answerability:
                reasons.add("answerability_adjudication_unavailable")
            if True in answerability and False in answerability:
                reasons.add("mixed_view_answerability_policy_unratified")
        if blocking[arm]["unknown_outcomes"]:
            reasons.add("terminal_false_block_judgment_unavailable")
        def confirmed(row):
            return (_judged_boolean(row, "answerable") is True
                    and _judged_boolean(row, "terminal_false_block") is True)
        blocking[arm]["confirmed_false_block_assignments"] = sorted(row["assignment_id"] for values in rows for row in values if confirmed(row))
        blocking[arm]["confirmed_false_block_studies"] = sum(any(confirmed(row) for row in values) for values in rows)
        blocking[arm]["unavailable_reasons"] = sorted(reasons)
        closures[arm] = _rate(sum(any(r["harmful_closure"] is True for r in values) for values in rows), len(rows),
                             sum(any(r["harmful_closure"] is None or r["adjudication"] is None or r["independent"] is not True
                                     for r in values) for values in rows))
        arm_attempts = [a for a in attempts.values() if assignments[a["assignment_id"]]["arm_id"] == arm]
        arm_usage = {key: usage_total(arm_attempts, key) for key in RESOURCE_KEYS}
        costs[arm] = {"usage": arm_usage, "assigned_studies": len(rows), "useful_answers": success,
            "unvalidated_declared_useful_answers": unvalidated, "unknown_usefulness_studies": unknown,
            "per_assigned_study": {k: v / len(rows) if v is not None and rows else None for k, v in arm_usage.items()},
            "per_useful_answer": {k: v / success if v is not None and success and not unknown else None for k, v in arm_usage.items()},
            "latency_p50": None, "latency_p95": None, "latency_definition": "unratified_no_implicit_quantile_rule"}
        if p["latency"]["method"] == "nearest_rank" and p["latency"]["unit"] == "assigned_study_wall_interval":
            durations = []
            for (_, cluster) in (key for key in grouped if key[0] == arm):
                selected = [a for a in arm_attempts if clusters[assignments[a["assignment_id"]]["study_id"]] == cluster]
                starts, ends = [_instant(a["started_at"]) for a in selected], [_instant(a["ended_at"]) for a in selected]
                if None in starts or None in ends:
                    break
                durations.append((max(ends) - min(starts)).total_seconds())
            if len(durations) == len(rows) and durations:
                durations.sort()
                costs[arm]["latency_p50"] = durations[math.ceil(.5 * len(durations)) - 1]
                costs[arm]["latency_p95"] = durations[math.ceil(.95 * len(durations)) - 1]
            costs[arm]["latency_definition"] = "declared_nearest_rank_assigned_study_wall_interval"
    statuses = {status: sum(a["status"] == status for a in attempts.values()) for status in Attempt.model_fields["status"].annotation.__args__}
    faults = {f"F{i}": {control: [r for r in ledger["fault_records"] if r["fault_id"] == f"F{i}" and r["control"] == control]
               for control in ("clean", "adversarial")} for i in range(1, 8)}
    return {"assigned_studies": len(set(clusters.values())),
        "assigned_arm_studies": len(grouped), "assignments": len(assignments), "attempts": len(attempts),
        "attempt_statuses": statuses, "retries": sum(a["retry_of"] is not None for a in attempts.values()),
        "unknown_usage_attempts": sorted(a["attempt_id"] for a in attempts.values() if any(v is None for v in a["usage"].values())),
        "usage": usage, "costs": costs, "interventions": [i for a in attempts.values() for i in a["interventions"]],
        "fdp": _fdp_scores(p, ledger, assignments, studies, clusters), "useful_completion": useful,
        "terminal_false_blocking": blocking, "harmful_closure_studies": closures, "review": _review_scores(ledger),
        "numerical": _numerical_scores(p, ledger, assignments, studies), "faults": faults,
        "critical_integrity": _integrity_scores(ledger, assignments, clusters),
        "paired_benefit": {"status": "inconclusive", "confidence_interval": None, "method": None, "benefit_claim": False},
        "x9": _x9_scores(p, ledger, assignments, outcomes, clusters), "model_calls": 0, "numerical_jobs": 0}


def _assignment_view(p, ledger, assignments, studies, clusters, *, endpoint, arm=None):
    """The same declared assignment scope feeds every endpoint metric family."""
    chosen = {key: a for key, a in assignments.items()
              if endpoint["endpoint_id"] in a["endpoint_ids"] and a["arm_id"] in endpoint["arm_ids"]
              and (arm is None or a["arm_id"] == arm)
              and all(studies[a["study_id"]][key] == endpoint[key] for key in ("partition", "regime", "stratum"))}
    selected_studies = {a["study_id"]: studies[a["study_id"]] for a in chosen.values()}
    view = {**ledger, "assignments": list(chosen.values())}
    for field in ("attempts", "outcomes", "review_packets", "numerical_records", "fault_records", "integrity_records"):
        view[field] = [row for row in ledger[field] if row["assignment_id"] in chosen]
    # Extra numerical observations stay in the global descriptive inventory; a
    # declared numerical endpoint reports its own expected quantity inventory.
    if endpoint["kind"] == "numerical_agreement":
        view["numerical_records"] = [row for row in view["numerical_records"] if row["quantity"] in endpoint["required_quantities"]]
    return ({**p, "endpoints": [endpoint]}, view, chosen, _index(view["attempts"], "attempt_id"),
            _index(view["outcomes"], "assignment_id"), selected_studies,
            {key: clusters[key] for key in selected_studies})


def _endpoint_scores(p, ledger, assignments, studies, clusters):
    result = {}
    review_kinds = {"review_detection": "review_detection", "initial_false_hold": "initial_false_holds",
                    "resolved_hold": "resolved_false_holds"}
    for ep in p["endpoints"]:
        view = _assignment_view(p, ledger, assignments, studies, clusters, endpoint=ep)
        _, scoped, selected, _, outcomes, _, selected_clusters = view
        report = {"endpoint": ep, "assignment_ids": sorted(selected), "study_clusters": len(set(selected_clusters.values())),
                  "attempt_count": len(scoped["attempts"]), "arms": {}, "comparison": None, "qualified": False}
        if ep["kind"] == "x9":
            report["comparison"] = _x9_scores(view[0], scoped, selected, outcomes, selected_clusters)
        for arm in ep["arm_ids"]:
            arm_view = _assignment_view(p, ledger, assignments, studies, clusters, endpoint=ep, arm=arm)
            summary = _assigned_summary(*arm_view)
            kind = ep["kind"]
            if kind in {"fdp", "global_null_any_discovery"}:
                metric = summary["fdp"][ep["endpoint_id"]][arm]
            elif kind in review_kinds:
                metric = summary["review"][review_kinds[kind]]
            elif kind == "harmful_closure":
                metric = (summary["review"]["harmful_closure_touchpoints"] if ep["denominator"] == "review_touchpoints"
                          else summary["harmful_closure_studies"].get(arm, _rate(0, 0)))
            elif kind in {"useful_completion", "terminal_false_block"}:
                metric = summary["useful_completion" if kind == "useful_completion" else "terminal_false_blocking"].get(arm, _rate(0, 0))
            elif kind == "numerical_agreement":
                metric = summary["numerical"]
            elif kind == "critical_integrity":
                metric = summary["critical_integrity"]
                field = {"integrity_cases": "case_denominator", "integrity_opportunities": "opportunity_denominator"}.get(
                    ep["denominator"], "assigned_study_denominator")
                metric = {**metric, "endpoint_denominator": metric[field]}
            elif kind in {"cost_burden", "x9"}:
                metric = summary["costs"].get(arm, {"assigned_studies": 0, "useful_answers": 0})
            else:  # No supported prospective paired interval exists in this release.
                metric = {**summary["paired_benefit"], "assigned_studies": summary["assigned_studies"]}
            report["arms"][arm] = metric
        result[ep["endpoint_id"]] = report
    return result


def account_release_campaign(protocol, value):
    validation = validate_release_protocol(protocol)
    # Preserve canonical caller bytes: pydantic float coercion is not a new freeze.
    ledger, assignments, attempts, outcomes, studies = _validate_ledger(protocol, value)
    clusters, _ = _clusters(protocol["studies"])
    overview = _assigned_summary(protocol, ledger, assignments, attempts, outcomes, studies, clusters)
    return {"format": "devbench.rna-release-score/1", "protocol_id": PROTOCOL_ID,
        "protocol_sha256": record_digest(protocol), "ledger_sha256": record_digest(value), "evaluator": validation["evaluator"],
        "scope": "descriptive_engineering_records_not_qualification", "admission": "blocked",
        "blockers": validation["blockers"], "qualification": _qualification(),
        "overview_scope": "all_assigned_descriptive_context_not_declared_endpoint_scores", **overview,
        "endpoint_scores": _endpoint_scores(protocol, ledger, assignments, studies, clusters)}


def _integrity_scores(ledger, assignments, clusters):
    rows = ledger["integrity_records"]
    cases = {(r["assignment_id"], r["case_id"]) for r in rows}
    opportunities = {(r["assignment_id"], r["opportunity_id"]) for r in rows}
    if len(opportunities) != len(rows):
        raise ValueError("duplicate_integrity_opportunity")
    accepted = [r for r in rows if r["critical_violation"] is True and r["accepted"] is True]
    unknown = sum(r["critical_violation"] is None or r["accepted"] is None or r["adjudication"] is None or r["independent"] is not True for r in rows)
    return {"accepted_critical_violations": len(accepted), "opportunity_denominator": len(opportunities),
            "case_denominator": len(cases), "assigned_study_denominator": len(set(clusters.values())),
            "affected_studies": len({clusters[assignments[r["assignment_id"]]["study_id"]] for r in accepted}),
            "unknown_opportunities": unknown, "confidence_interval": None, "qualified": False}


def _inside(path, workspace):
    path, workspace = Path(path), Path(workspace)
    if (not path.is_absolute() or not workspace.is_absolute() or ".." in path.parts or ".." in workspace.parts
            or any(part.is_symlink() for part in (path, *path.parents, workspace, *workspace.parents))
            or not path.is_relative_to(workspace) or path == workspace):
        raise ValueError("release_path_requires_absolute_assigned_workspace_without_symlinks")
    return path


def read_release_record(path, *, workspace):
    return decode_json(_read_regular(_inside(path, workspace), MAX_RECORD_BYTES))


def _read_regular(path, maximum):
    """Reuse bounded reads, additionally rejecting aliases and changed snapshots."""
    before = path.lstat()
    if before.st_nlink != 1:
        raise ValueError("release_record_hardlink_alias_forbidden")
    raw = read_bounded_file(path, maximum)
    after = path.lstat()
    def signature(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)
    if signature(before) != signature(after):
        raise ValueError("release_record_changed_during_read")
    return raw


def _fresh(output, workspace):
    path = _inside(output, workspace)
    if path.exists() or not path.parent.is_dir():
        raise ValueError("release_output_must_be_fresh_with_existing_parent")
    return path


def _write_members(output, members):
    output.mkdir(mode=0o700)
    for name, raw in members.items():
        descriptor = os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())


def _implementation():
    path = Path(__file__).absolute()
    return {"name": "devbench.analysis.rna_release_evaluation", "sha256": hashlib.sha256(read_bounded_file(path, MAX_RECORD_BYTES)).hexdigest()}


def write_score_package(output, *, protocol, ledger, workspace):
    output = _fresh(output, workspace)
    scores = account_release_campaign(protocol, ledger)
    members = {"protocol.json": canonical_json_bytes(protocol), "ledger.json": canonical_json_bytes(ledger), "scores.json": canonical_json_bytes(scores)}
    if sum(map(len, members.values())) > MAX_PACKAGE_BYTES or any(len(raw) > MAX_RECORD_BYTES for raw in members.values()):
        raise ValueError("release_score_package_byte_bound")
    manifest = {"format": PACKAGE_FORMAT, "protocol_id": PROTOCOL_ID, "evaluator": _implementation(),
        "scope": "data_only_engineering_score_replay", "members": [
            {"name": name, "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)} for name, raw in sorted(members.items())]}
    raw = canonical_json_bytes(manifest)
    _write_members(output, {**members, "manifest.json": raw})
    return {"root_sha256": hashlib.sha256(raw).hexdigest(), "scores_sha256": hashlib.sha256(members["scores.json"]).hexdigest(),
            "package": str(output), "admission": "blocked", "qualification": _qualification()}


def replay_release_scores(package, expected_root, output, *, workspace):
    package, output = _inside(package, workspace), _fresh(output, workspace)
    if output.is_relative_to(package):
        raise ValueError("release_replay_output_must_not_modify_original_package")
    names = set()
    if package.is_dir():
        for entry in package.iterdir():
            names.add(entry.name)
            if len(names) > 4:
                break
    if names != {"manifest.json", "protocol.json", "ledger.json", "scores.json"}:
        raise ValueError("release_score_package_closed_inventory_required")
    raw = _read_regular(package / "manifest.json", 100_000)
    if hashlib.sha256(raw).hexdigest() != expected_root:
        raise ValueError("release_score_package_root_mismatch")
    manifest = decode_json(raw)
    if (type(manifest) is not dict or set(manifest) != {"format", "protocol_id", "evaluator", "scope", "members"}
            or manifest["format"] != PACKAGE_FORMAT or manifest["protocol_id"] != PROTOCOL_ID
            or manifest["scope"] != "data_only_engineering_score_replay" or manifest["evaluator"] != _implementation()
            or type(manifest["members"]) is not list or len(manifest["members"]) != 3):
        raise ValueError("release_score_package_identity_changed")
    members, total = {}, 0
    for binding in manifest["members"]:
        if (type(binding) is not dict or set(binding) != {"name", "sha256", "size_bytes"}
                or binding["name"] not in {"protocol.json", "ledger.json", "scores.json"} or binding["name"] in members
                or type(binding["size_bytes"]) is not int or not 0 <= binding["size_bytes"] <= MAX_RECORD_BYTES):
            raise ValueError("release_score_package_unsafe_logical_member")
        content = _read_regular(package / binding["name"], MAX_RECORD_BYTES)
        total += len(content)
        if (len(content) != binding["size_bytes"] or hashlib.sha256(content).hexdigest() != binding["sha256"]
                or total > MAX_PACKAGE_BYTES):
            raise ValueError("release_score_package_member_changed")
        members[binding["name"]] = content
    protocol, ledger = decode_json(members["protocol.json"]), decode_json(members["ledger.json"])
    scores = canonical_json_bytes(account_release_campaign(protocol, ledger))
    if scores != members["scores.json"]:
        raise ValueError("release_score_replay_mismatch")
    receipt = {"format": "devbench.rna-release-score-replay/1", "original_root_sha256": expected_root, "evaluator": _implementation(),
        "scores_sha256": hashlib.sha256(scores).hexdigest(), "model_calls": 0, "numerical_jobs": 0,
        "hidden_answer_reads": 0, "admission": "blocked", "qualification": _qualification()}
    _write_members(output, {"scores.json": scores, "replay-receipt.json": canonical_json_bytes(receipt)})
    return receipt


def add_release_arguments(parser, modes, *, has_workspace=False):
    parser.set_defaults(_release_workspace_only=not has_workspace)
    for mode in ("validate", "score", "replay"):
        modes.add_argument("--v24-" + mode, action="store_true")
    if not has_workspace:
        parser.add_argument("--workspace")
    parser.add_argument("--protocol", help="Closed v2.4 prospective protocol JSON")
    parser.add_argument("--ledger", help="Closed post-seal v2.4 accounting records")
    parser.add_argument("--package", help="Data-only v2.4 score package")
    parser.add_argument("--expected-v24-root")


def dispatch_release_cli(args, parser):
    """Called before legacy MethodService, Planner, or campaign construction."""
    import json
    if not (args.v24_validate or args.v24_score or args.v24_replay):
        if (any(getattr(args, key) is not None for key in ("protocol", "ledger", "package", "expected_v24_root"))
                or args._release_workspace_only and args.workspace is not None):
            parser.error("v2.4-only inputs require an explicit v2.4 mode and cannot enter a historical runner")
        return None
    legacy_inputs = ("method_workspace", "request", "manifest", "expected_root", "expected_score_seal", "admission")
    if any(getattr(args, key, None) is not None for key in legacy_inputs) or getattr(args, "synthetic_tools", False):
        parser.error("v2.4 modes do not accept historical trial or campaign inputs")
    if not args.workspace:
        parser.error("v2.4 modes require --workspace")
    if args.v24_replay:
        if args.protocol is not None or args.ledger is not None:
            parser.error("--v24-replay accepts a score package, not separate protocol or ledger inputs")
        if not args.package or not args.expected_v24_root or not args.output:
            parser.error("--v24-replay requires --package, --expected-v24-root and fresh --output")
        result = replay_release_scores(args.package, args.expected_v24_root, args.output, workspace=args.workspace)
    else:
        if args.package is not None or args.expected_v24_root is not None:
            parser.error("v2.4 validation/scoring does not accept replay inputs")
        if args.v24_validate and (args.ledger is not None or args.output is not None):
            parser.error("--v24-validate accepts only the protocol and workspace")
        if not args.protocol:
            parser.error("v2.4 validation/scoring requires --protocol")
        protocol = read_release_record(args.protocol, workspace=args.workspace)
        if args.v24_validate:
            result = validate_release_protocol(protocol)
        else:
            if not args.ledger or not args.output:
                parser.error("--v24-score requires --ledger and fresh --output")
            ledger = read_release_record(args.ledger, workspace=args.workspace)
            result = write_score_package(args.output, protocol=protocol, ledger=ledger, workspace=args.workspace)
    print(json.dumps(result, allow_nan=False))
    return 2 if args.v24_validate and result.get("admission") == "blocked" else 0
