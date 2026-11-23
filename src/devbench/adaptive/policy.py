"""Deterministic policy B and admitted controller proposals.

Policy B dispatches from predeclared rules. A controller proposal is not work
until the coordinator admits it. Neither policy qualifies an inferential claim.
"""

from __future__ import annotations

import hashlib

from devbench.adaptive.ledger import InvestigationLedger
from devbench.adaptive.models import (
    BlockedStep,
    DependencyEdge,
    DerivedArtifact,
    InspectionSpec,
    PlanVersion,
    Port,
    PortFulfillment,
    ResourceGrant,
    StepOutcome,
    StepSpec,
    TransformSpec,
    UseInvalidation,
    seal,
)
from devbench.engine.provenance import canonical_json

GRANT = ResourceGrant(cpu_seconds=30, memory_mb=512, numerical_jobs=1)
THRESHOLD = 0.5


def score_spread(values: list[float]) -> float:
    return float(max(values) - min(values))


def build_conditional_plan() -> PlanVersion:
    inspect = _step(
        "inspect",
        InspectionSpec(
            payload_type="inspection",
            selectors=("scores",),
            dimensions=("samples",),
            known_provenance=("derived-scores",),
        ),
        (),
        (),
        (Port(name="value", artifact_kind="table"),),
    )
    sensitivity = _step(
        "sensitivity",
        TransformSpec(
            payload_type="transform",
            algorithm="score_spread",
            axis_mappings=("samples",),
            invariants=("predeclared threshold",),
            output_representation="table",
        ),
        ("inspect",),
        (Port(name="value", artifact_kind="table"),),
        (Port(name="value", artifact_kind="table"),),
    )
    edge = DependencyEdge(
        edge_id="inspect-sensitivity",
        source_id="inspect",
        target_id="sensitivity",
        kind="compute",
        reason="conditional on score spread",
        source_port="value",
        target_port="value",
    )
    return seal(
        PlanVersion,
        plan_id="plan-policy",
        revision=0,
        parent_revision=None,
        question_id="q-policy",
        nodes=(inspect, sensitivity),
        edges=(edge,),
        retired=("sensitivity",),
        rationale="sensitivity stays inactive until the predeclared spread threshold is met",
        expected_observation="either no follow-up or a new sensitivity result",
    )


def policy_b(ledger: InvestigationLedger, values: list[float], source_digest: str) -> str:
    """Deterministic activation. This does not open a model session."""
    spread = score_spread(values)
    if spread <= THRESHOLD:
        return "HELD"
    return _activate(ledger, spread, source_digest, "B")


def admit_controller(ledger: InvestigationLedger, proposal: dict, values: list[float], source_digest: str) -> str:
    """Admit one controller proposal. Promotion and holdout reads are refused."""
    if proposal.get("policy") != "C" or proposal.get("promote") or proposal.get("read_holdout"):
        raise RuntimeError("Legacy controller rejection journaling is quarantined")
    return _activate(ledger, score_spread(values), source_digest, "C")


def publish_inspection(ledger: InvestigationLedger) -> str:
    _publish(ledger, "inspect", "", {"observed_samples": 4})
    return ledger.view().artifacts[-1].content_digest


def run_sensitivity(ledger: InvestigationLedger, values: list[float], parent_digest: str) -> str:
    if "sensitivity" not in ledger.view().ready_frontier():
        return "NOT_READY"
    payload = {"spread": score_spread(values), "threshold": THRESHOLD, "activated": True}
    _publish(ledger, "sensitivity", parent_digest, payload)
    return "COMPLETED"


def _quarantined_sweep():
    raise RuntimeError("Legacy adaptive sweeps are quarantined")


def neighborhood_sweep(samples: list[str], values: list[list[float]]):
    return _quarantined_sweep()


def execute_sweep(ledger: InvestigationLedger, matrix: dict) -> str:
    return _quarantined_sweep()


def repeat_member(ledger: InvestigationLedger, member_id: str, parameter: str, stream_id: str) -> str:
    return _quarantined_sweep()


def fail_member(ledger: InvestigationLedger, sweep_id: str, member_id: str, parameter: str, display_name: str) -> str:
    return _quarantined_sweep()


# The held-out worker applies a frozen training model and releases a grouped scalar.
GROUPED_EVALUATOR_SOURCE = """\
import json
import math
import statistics
from pathlib import Path

def read(port):
    return json.loads(Path('/inputs/' + port).read_bytes())

def require(condition, message):
    if not condition:
        raise ValueError(message)

data, model = read('held'), read('model')
centroids = model['centroids']
mean = model['mean']
features = data['features']
require(features and features == model['features'], 'Evaluation features must match the frozen model')
require(type(mean) in (int, float) and math.isfinite(mean), 'Frozen model mean must be finite')
require(type(centroids) is dict and len(centroids) >= 2, 'Frozen model requires at least two centroids')
require(all(type(v) in (int, float) and math.isfinite(v) for v in centroids.values()),
        'Frozen model centroids must be finite')
units = {}
for row in data['rows']:
    label = row['label']
    require(label in centroids, 'Evaluation label is outside the frozen model')
    recorded, scores = units.setdefault(row['unit'], (label, []))
    require(recorded == label, 'Evaluation labels disagree within a biological unit')
    counts = row['counts']
    require(len(counts) == len(features) and all(type(v) is int and v >= 0 for v in counts),
            'Evaluation counts must match the frozen features and be nonnegative integers')
    total = sum(counts)
    require(total > 0, 'Evaluation library size must be positive')
    scores.append(math.log1p(counts[0] / total * 10000) - mean)
require(units, 'Evaluation requires at least one biological unit')
correct = sum(min(centroids, key=lambda key: (abs(statistics.mean(scores) - centroids[key]), key))
              == label for label, scores in units.values())
Path('/outputs/out.json').write_text(json.dumps({'units':len(units), 'accuracy':correct/len(units)},
                                              allow_nan=False))
"""


def seal_holdout(partition, manifest) -> str:
    from devbench.engine.partition_authority import PartitionAuthority

    if type(partition) is not PartitionAuthority:
        raise PermissionError("Legacy holdout execution is quarantined")
    return partition.seal(manifest)


def access_holdout(*_args, **_kwargs) -> dict:
    raise PermissionError("Protected partitions can only be opened by the admitted OCI evaluator")


def authorize_holdout(workflow, step_id: str) -> str:
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow

    if type(workflow) is not AdaptiveWorkflow:
        raise PermissionError("Legacy holdout execution is quarantined")
    return workflow.authorize_protected_step(step_id)


def record_contradiction(ledger: InvestigationLedger, artifact: DerivedArtifact) -> str:
    ledger.invalidate_use(
        UseInvalidation(
            artifact_digest=artifact.content_digest,
            use="qualified_inference",
            reason="condition and batch are completely confounded",
            change_class="contradictory_evidence",
        )
    )
    ledger.block_step(
        BlockedStep(
            step_id="adjusted-fit",
            spec_digest=artifact.producer_spec_digest,
            purpose="batch-adjusted treatment effect",
            missing_condition="the adjusted treatment effect is not identifiable",
            inspected_evidence=(artifact.content_digest,),
            affected_output_ports=("effect",),
            affected_descendants=(),
            affected_claims=("batch-adjusted treatment effect",),
            completed_independent_work=(),
            resumption_predicate="a design with crossed condition and batch is supplied",
            human_input_needed=False,
        )
    )
    return "BLOCKED"


def _activate(ledger: InvestigationLedger, spread: float, source_digest: str, policy_id: str) -> str:
    plan = ledger.view().active_plan()
    if plan is None or "sensitivity" not in plan.retired:
        return "UNAVAILABLE"
    revised = seal(
        PlanVersion,
        plan_id=plan.plan_id,
        revision=plan.revision + 1,
        parent_revision=plan.revision,
        question_id=plan.question_id,
        nodes=plan.nodes,
        edges=plan.edges,
        inserted=("sensitivity",),
        retired=(),
        triggering_evidence=(source_digest, f"spread={spread}"),
        rationale=f"policy {policy_id} activated the conditional sensitivity because the spread exceeded {THRESHOLD}",
        expected_observation="a sensitivity table",
        claim_consequences=("exploratory sensitivity only",),
    )
    return ledger.propose_plan(revised.model_dump(mode="json"), expected_revision=plan.revision)


def _publish(ledger: InvestigationLedger, step_id: str, parent_digest: str, payload: dict) -> None:
    node = next(item for item in ledger.view().active_plan().nodes if item.step_id == step_id)
    body = canonical_json(payload)
    attempt_id = f"attempt-{step_id}"
    reservation = ledger.reserve_attempt(
        {
            "attempt_id": attempt_id,
            "idempotency_key": f"policy-{step_id}",
            "spec_digest": node.content_digest,
            "plan_revision": ledger.view().active_revision,
            "fence": f"fence-{step_id}",
            "state": "reserved",
            "backend": "local",
        }
    )
    if reservation != "attempt_reserved":
        raise RuntimeError("Policy attempt reservation was refused")
    artifact = seal(
        DerivedArtifact,
        kind="table",
        producer_attempt_id=attempt_id,
        producer_spec_digest=node.content_digest,
        parent_digests=(parent_digest,) if parent_digest else (),
        axes=("sample",),
        stable_id_digest=hashlib.sha256(body).hexdigest(),
        dimensions=(1,),
        storage_layout="canonical-json",
        units="score spread",
        measurement_meaning="exploratory sensitivity",
        original_population="study samples",
        derived_population="study samples",
        algorithm="score_spread",
        environment="local-isolated",
        parameters_digest=hashlib.sha256(body).hexdigest(),
        randomness="none",
        permitted_uses=("exploratory",),
        byte_integrity="verified",
        schema_compatible=True,
        scientific_applicability="experimental",
        selection_status="none",
    )
    if ledger.publish_artifact(artifact) != "artifact_published":
        raise RuntimeError("Policy artifact publication was refused")
    outcome = ledger.record_outcome(
        StepOutcome(
            step_id=step_id,
            attempt_id=attempt_id,
            execution_status="completed",
            ports=(PortFulfillment(port="value", status="fulfilled", artifact_id=artifact.content_digest),),
        )
    )
    if outcome != "outcome_recorded":
        raise RuntimeError("Policy outcome recording was refused")


def _step(step_id, operation, dependencies, inputs, outputs):
    return seal(
        StepSpec,
        step_id=step_id,
        question_id="q-policy",
        plan_id="plan-policy",
        branch_id="policy",
        purpose=step_id,
        expected_information=step_id,
        dependencies=dependencies,
        input_ports=inputs,
        output_ports=outputs,
        operation=operation,
        evidence_use_scope="structural_only",
        source_environment="local-isolated",
        resources=GRANT,
        completion_obligations=("value",),
    )
