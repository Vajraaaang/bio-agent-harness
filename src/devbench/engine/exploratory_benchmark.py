"""Receipt-backed Tier B comparison with one protected donor-level evaluation."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from devbench.core._files import hash_bounded_file
from devbench.core._hashing import canonical_sha256, decode_receipt_json
from devbench.engine.workflow_operations import OperationBlocked, OperationConflict
from devbench.platform.adaptive_workflow import (
    AdaptiveStep,
    AdaptiveWorkflow,
    request_digest,
)
from devbench.platform.adaptive_workflow_state import Digest, Identity

from .exploratory_oci import ExploratoryOCI
from .method_executor import EXPLORATORY_IMAGE, EXPLORATORY_IMAGE_ID
from .methods import exploratory_method
from .partition_authority import PartitionAuthority
from .provenance import canonical_json
from .scientific_authority import _lookup

_OPERATION = "exploratory_grouped_benchmark"
_FREEZE_EVENT = "exploratory_benchmark_approved"
_WORKER = Path(__file__).resolve().with_name("exploratory_benchmark_worker.py")
_PORTS = {
    "training": "counts",
    "held": "counts",
    "candidate": "model",
    "baseline": "model",
}
_LIMITS = {
    "cpu": 1,
    "memory_mb": 512,
    "timeout_seconds": 30,
    "pids": 32,
    "max_output_bytes": 65_536,
    "network": "deny",
}
_CLAIM = {
    "claimed_scope": "exploratory_only",
    "implementation_trust": "unreviewed",
    "clinical_release_allowed": False,
}
_FROZEN_LOCK = canonical_json(
    {
        "format": "devbench.grouped-benchmark-lock/1",
        "image_id": EXPLORATORY_IMAGE_ID,
        "dependencies": [],
    }
)
_ID = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.:-]{1,90}$")]
_Statement = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]
_Probability = Annotated[float, Field(ge=0, le=1)]
_Seed = Annotated[int, Field(ge=0, lt=2**32)]


class _Record(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, allow_inf_nan=False
    )


class BenchmarkArm(_Record):
    """The precise existing step, method and settings subject to approval."""

    step_id: Identity
    method_version_id: Digest
    parameters_sha256: Digest
    model_port: Identity


class ExploratoryBenchmarkPlan(_Record):
    """Prospective scoped comparison of two grouped expression trainers."""

    benchmark_id: _ID
    cohort_sha256: Digest
    candidate: BenchmarkArm
    baseline: BenchmarkArm
    training_version_id: Identity
    evaluation_version_id: Identity
    evaluation_step_id: Identity
    hypothesis: _Statement
    baseline_rationale: _Statement
    minimum_balanced_accuracy_gain: Annotated[float, Field(gt=0, le=1)]
    maximum_worst_class_error_increase: _Probability
    null_seed: _Seed

    @model_validator(mode="after")
    def _distinct_roster(self):
        steps = (self.candidate.step_id, self.baseline.step_id, self.evaluation_step_id)
        implementations = {
            (arm.method_version_id, arm.parameters_sha256)
            for arm in (self.candidate, self.baseline)
        }
        if len(set(steps)) != 3 or len(implementations) != 2:
            raise ValueError("Benchmark arms require distinct procedures and step IDs")
        if self.training_version_id == self.evaluation_version_id:
            raise ValueError("Benchmark inputs require different partition versions")
        return self


class _ArmScores(_Record):
    accuracy: _Probability
    balanced_accuracy: _Probability
    worst_class_error: _Probability


class _ArmNumbers(_Record):
    candidate: _Probability
    baseline: _Probability


class _NegativeControl(_Record):
    method: Literal["donor_label_permutation"]
    seed: _Seed
    repetitions: Literal[64]
    mean_balanced_accuracy: _ArmNumbers


class _Scores(_Record):
    format: Literal["devbench.grouped-benchmark-scores/1"]
    held_out_donors: Annotated[int, Field(ge=4, le=2048)]
    class_count: Annotated[int, Field(ge=2, le=32)]
    arms: dict[Literal["candidate", "baseline"], _ArmScores]
    negative_control: _NegativeControl

    @model_validator(mode="after")
    def _axes(self):
        if set(self.arms) != {"candidate", "baseline"}:
            raise ValueError("Evaluation must compare both bound arms")
        if self.held_out_donors < 2 * self.class_count:
            raise ValueError("Insufficient donors for the reported classes")
        return self


def _record_id(benchmark_id: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_.:-]{1,90}", benchmark_id) is None:
        raise ValueError("Use a bounded benchmark ID")
    return f"benchmark-{benchmark_id}"


def _owner(flow: AdaptiveWorkflow, executor: ExploratoryOCI) -> PartitionAuthority:
    partition = executor.partition
    if (
        type(flow) is not AdaptiveWorkflow
        or type(executor) is not ExploratoryOCI
        or executor.ledger.path != flow.ledger.path
        or type(partition) is not PartitionAuthority
        or flow.partition_check is not partition
        or partition.authority.ledger.path != flow.ledger.path
    ):
        raise PermissionError(
            "Benchmark requires its bound Tier B and partition authority"
        )
    return partition


def _load_record(db, flow, identity):
    row = _lookup(db, flow.lineage_id, _record_id(identity))
    if row is None:
        raise OperationBlocked("exploratory_benchmark_approval_required")
    record = row["record"]
    if record["event"] != _FREEZE_EVENT or record["kind"] != "execution_event":
        raise OperationConflict("Benchmark approval record has a different event type")
    document = record["state"]
    plan = ExploratoryBenchmarkPlan.model_validate(document["plan"])
    if plan.benchmark_id != identity:
        raise OperationConflict("Benchmark approval has a different identity")
    return plan, document, row["sha256"]


def _arm_steps(flow, db, plan, executor):
    state = flow._state(db)
    steps = {}
    limits = []
    for name, arm in (("candidate", plan.candidate), ("baseline", plan.baseline)):
        node = state.nodes.get(arm.step_id)
        if node is None or not _matches_trainer(node, arm, plan.training_version_id):
            raise PermissionError(
                "Benchmark frozen arm does not match its exploratory step"
            )
        envelope = flow._envelope(state, arm.step_id, "benchmark-check", db=db)
        _, method, _ = executor.validate(envelope)
        contract = method["contract"]
        if contract["outputs"][arm.model_port]["artifact_type"] != "json":
            raise PermissionError("Benchmark trainer must emit a model JSON artifact")
        limits.append(contract["execution"])
        steps[name] = _step_identity(state, node)
    if limits[0] != limits[1]:
        raise ValueError("Matched benchmark trainers require the same resource limits")
    return state, steps


def _matches_trainer(step, arm, version):
    if (
        step.purpose != "exploratory"
        or step.held_out
        or step.primary
        or step.role != "compute"
        or step.oci_method_version_id != arm.method_version_id
        or step.parameters_sha256 != arm.parameters_sha256
        or len(step.inputs) != 1
    ):
        return False
    source = step.inputs[0]
    return (
        source.source_id,
        source.source_port,
        source.port.artifact_kind,
        source.use,
    ) == (version, None, "counts", "compute") and any(
        (port.name, port.artifact_kind, port.required)
        == (arm.model_port, "model", True)
        for port in step.outputs
    )


def _step_identity(state, node):
    return {
        "step_sha256": request_digest(node.model_dump(mode="json")),
        "generation": state.generations[node.step_id],
    }


def _partition_manifest(db, partition, plan):
    if partition.lineage != "partition-" + plan.cohort_sha256:
        raise PermissionError("Benchmark cohort differs from the admitted partition")
    record = _lookup(db, partition.lineage, "partition-manifest")
    if record is None or record["record"]["event"] != "partition_sealed":
        raise OperationBlocked("exploratory_benchmark_partition_not_sealed")
    return record


def _check_artifacts(state, plan, manifest):
    units = set()
    for role, version in (
        ("training", plan.training_version_id),
        ("evaluation", plan.evaluation_version_id),
    ):
        record = state.artifacts.get(version)
        if record is None or record[1] is not None or version in state.stale_versions:
            raise PermissionError(
                "Benchmark requires current original partition artifacts"
            )
        value = record[0]
        if (
            value["sha256"] != manifest[role]["sha256"]
            or value["unit_membership_sha256"] != manifest[role]["units_sha256"]
            or value["kind"] != "counts"
            or value["representation"] != "COUNT-1"
            or value["learned_scope"] != "none"
            or value["label_origin"] != "independent"
        ):
            raise PermissionError(
                "Benchmark partition needs independent original-count inputs"
            )
        units.add(value["biological_unit"])
    if len(units) != 1:
        raise PermissionError("Benchmark needs a shared declared biological unit")
    return next(iter(units))


def _parameters(plan, manifest):
    return {
        "benchmark_id": plan.benchmark_id,
        "null_seed": plan.null_seed,
        "training_units_sha256": manifest["training"]["units_sha256"],
        "held_units_sha256": manifest["evaluation"]["units_sha256"],
    }


def _port(name, kind, source_id, source_port=None):
    return {
        "port": {"name": name, "artifact_kind": kind},
        "source_id": source_id,
        "source_port": source_port,
    }


def _build_evaluator(plan, version_id, parameters, unit):
    from hashlib import sha256

    return AdaptiveStep.model_validate(
        {
            "step_id": plan.evaluation_step_id,
            "branch_id": _record_id(plan.benchmark_id),
            "operation_id": _OPERATION,
            "environment_sha256": canonical_sha256(
                {
                    "image": EXPLORATORY_IMAGE,
                    "image_id": EXPLORATORY_IMAGE_ID,
                    "lock_sha256": sha256(_FROZEN_LOCK).hexdigest(),
                }
            ),
            "parameters_sha256": canonical_sha256(parameters),
            "oci_method_version_id": version_id,
            "purpose": "protected_evaluation",
            "held_out": True,
            "shared_units": False,
            "profile": {
                "profile_id": "exploratory-grouped-benchmark",
                "version": 1,
                "operations": [
                    {
                        "operation_id": _OPERATION,
                        "output_kinds": ["scores"],
                        "max_memory_mb": _LIMITS["memory_mb"],
                        "timeout_seconds": _LIMITS["timeout_seconds"],
                    }
                ],
                "input_representations": ["COUNT-1", "model"],
                "biological_units": [unit],
                "permitted_claims": ["exploratory_finding"],
            },
            "inputs": [
                _port("training", "counts", plan.training_version_id),
                _port("held", "counts", plan.evaluation_version_id),
                _port(
                    "candidate",
                    "model",
                    plan.candidate.step_id,
                    plan.candidate.model_port,
                ),
                _port(
                    "baseline", "model", plan.baseline.step_id, plan.baseline.model_port
                ),
            ],
            "outputs": [{"name": "out", "artifact_kind": "scores"}],
        }
    )


def _register_evaluator(executor, actor):
    from hashlib import sha256

    properties = {
        "benchmark_id": {"type": "string", "maxLength": 90},
        "null_seed": {"type": "integer", "minimum": 0, "maximum": 2**32 - 1},
        "training_units_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        "held_units_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
    }
    contract = {
        "format": "devharness.method/1",
        "method_id": "devbench.exploratory-grouped-benchmark",
        "name": "Frozen grouped matched benchmark",
        "lifecycle": "custom",
        "implementation_trust": "unreviewed",
        "inputs": {
            name: {"artifact_type": "json", "required": True} for name in _PORTS
        },
        "outputs": {
            "out": {
                "artifact_type": "json",
                "path": "/outputs/out.json",
                "required": True,
            }
        },
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
        "implementation": {
            "kind": "oci",
            "entrypoint": ["python", "/method/main.py"],
            "source_sha256": hash_bounded_file(_WORKER, 1_000_000),
        },
        "environment": {
            "image": EXPLORATORY_IMAGE,
            "lock_sha256": sha256(_FROZEN_LOCK).hexdigest(),
        },
        "execution": _LIMITS,
        "validation": {
            "comparator": "json",
            "invariants": [],
            "counts_layer": "X",
            "qc_metrics": [],
        },
        "provenance": {"origin": "fixed_grouped_scoring"},
    }
    exploratory_method(contract)
    with TemporaryDirectory(dir=executor.root) as folder:
        lock = Path(folder) / "environment.lock"
        lock.write_bytes(_FROZEN_LOCK)
        return executor.methods.register_method(
            contract,
            source=_WORKER,
            environment_lock=lock,
            actor=actor,
        )


def _freeze_record(db, flow, state, attestation, evaluator):
    identity = _record_id(attestation["plan"]["benchmark_id"])
    if any(
        item["envelope"]["step"]["step_id"]
        in {
            attestation["plan"]["candidate"]["step_id"],
            attestation["plan"]["baseline"]["step_id"],
            evaluator.step_id,
        }
        for item in state.dispatches.values()
    ):
        raise PermissionError(
            "Benchmark approval must precede any arm or evaluation dispatch"
        )
    if evaluator.step_id in state.nodes:
        raise OperationConflict("Benchmark evaluation step ID is already in use")
    flow.authority._append_execution_record(
        db,
        flow.lineage_id,
        {
            "kind": "execution_event",
            "record_id": identity,
            "event": _FREEZE_EVENT,
            "state": attestation,
            "state_sha256": request_digest(attestation),
        },
    )
    row = _lookup(db, flow.lineage_id, identity)
    flow.insert(evaluator, evidence_sha256=row["sha256"], connection=db)
    return row["sha256"]


def freeze_exploratory_benchmark(
    flow: AdaptiveWorkflow,
    executor: ExploratoryOCI,
    plan: ExploratoryBenchmarkPlan,
    *,
    actor: str,
    rationale: str,
    authorize_operator: Callable[[str], bool],
) -> dict[str, Any]:
    """Approve exact arms before results and register one protected evaluation step."""
    partition = _owner(flow, executor)
    plan = ExploratoryBenchmarkPlan.model_validate(plan)
    if (
        re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", actor) is None
        or not callable(authorize_operator)
        or authorize_operator(actor) is not True
        or not rationale.strip()
        or len(rationale) > 2000
    ):
        raise PermissionError(
            "Benchmark approval requires an authorized operator and rationale"
        )
    with flow.authority._execution_tx(flow.lineage_id) as db:
        record = _lookup(db, flow.lineage_id, _record_id(plan.benchmark_id))
        if record is not None:
            return _resume_frozen(db, flow, executor, plan, actor, rationale)
        if partition._status(db) != "sealed":
            raise OperationBlocked("exploratory_benchmark_partition_unavailable")
        source = _partition_manifest(db, partition, plan)
        manifest = source["record"]["state"]
        state, arms = _arm_steps(flow, db, plan, executor)
        unit = _check_artifacts(state, plan, manifest)
        parameters = _parameters(plan, manifest)
        method = _register_evaluator(executor, actor)
        evaluator = _build_evaluator(plan, method["version_id"], parameters, unit)
        attestation = {
            "plan": plan.model_dump(mode="json"),
            "actor": actor,
            "rationale": rationale,
            "approved_at": datetime.now(UTC).isoformat(),
            "partition_sha256": source["sha256"],
            "arms": arms,
            "evaluator": evaluator.model_dump(mode="json"),
            "parameters": parameters,
        }
        digest = _freeze_record(db, flow, state, attestation, evaluator)
    return _activate(executor, plan, attestation, digest)


def _activate(executor, plan, approval, digest):
    evaluator = AdaptiveStep.model_validate(approval["evaluator"])
    parameters = approval["parameters"]
    if canonical_sha256(parameters) != evaluator.parameters_sha256:
        raise OperationConflict("Benchmark evaluator parameters changed")
    executor.parameters[evaluator.parameters_sha256] = canonical_json(parameters)
    return {
        "benchmark_id": plan.benchmark_id,
        "approval_sha256": digest,
        "evaluation_step_id": evaluator.step_id,
        **_CLAIM,
    }


def _resume_frozen(db, flow, executor, plan, actor, rationale):
    registered, approval, digest = _load_record(db, flow, plan.benchmark_id)
    if (registered, approval["actor"], approval["rationale"]) != (
        plan,
        actor,
        rationale,
    ):
        raise OperationConflict("Benchmark approval is immutable")
    return _activate(executor, registered, approval, digest)


def _assert_current(state, plan, approval):
    for name, arm in (("candidate", plan.candidate), ("baseline", plan.baseline)):
        node = state.nodes.get(arm.step_id)
        if (
            node is None
            or node.branch_id in state.retired
            or _step_identity(state, node) != approval["arms"][name]
        ):
            raise OperationBlocked("exploratory_benchmark_superseded")
    evaluator = state.nodes.get(plan.evaluation_step_id)
    if (
        evaluator is None
        or evaluator.model_dump(mode="json") != approval["evaluator"]
        or evaluator.branch_id in state.retired
    ):
        raise OperationBlocked("exploratory_benchmark_superseded")


def resume_exploratory_benchmark(
    flow: AdaptiveWorkflow,
    executor: ExploratoryOCI,
    benchmark_id: str,
) -> dict[str, Any]:
    """Rebind approved scorer settings after restarting the coordinator."""
    partition = _owner(flow, executor)
    with flow._read_connection() as db:
        plan, approval, digest = _load_record(db, flow, benchmark_id)
        _require_partition(db, partition, plan, approval)
        _assert_current(flow._state(db), plan, approval)
    return _activate(executor, plan, approval, digest)


def _require_partition(db, partition, plan, approval):
    if (
        _partition_manifest(db, partition, plan)["sha256"]
        != approval["partition_sha256"]
    ):
        raise OperationConflict("Benchmark approval refers to a different partition")


def verify_benchmark_evaluator(executor, envelope, contract, parameters):
    """The only new protected execution route is the frozen comparative scorer."""
    flow = AdaptiveWorkflow(
        executor.ledger,
        envelope["lineage_id"],
        partition_check=executor.partition,
    )
    partition = _owner(flow, executor)
    if (
        contract["implementation"]["source_sha256"]
        != hash_bounded_file(_WORKER, 1_000_000)
        or {name: item["kind"] for name, item in envelope["inputs"].items()} != _PORTS
        or contract["outputs"]
        != {
            "out": {
                "path": "/outputs/out.json",
                "artifact_type": "json",
                "required": True,
            }
        }
        or "benchmark_id" not in parameters
    ):
        raise PermissionError(
            "Protected evaluation requires the exact frozen benchmark scorer"
        )
    with flow._read_connection() as db:
        plan, approval, _ = _load_record(db, flow, parameters["benchmark_id"])
        if (
            approval["parameters"] != parameters
            or approval["evaluator"] != envelope["step"]
        ):
            raise PermissionError(
                "Protected evaluation differs from its benchmark approval"
            )
        _require_partition(db, partition, plan, approval)
        _assert_current(flow._state(db), plan, approval)


def _receipt(executor, state, step_id):
    outcome = state.outcome(step_id)
    if outcome is None:
        return {"status": "not_run", "reason": None, "billed_cost": None}, None
    retained = executor._retained_receipt(state, step_id)
    if retained is None:
        return {
            "status": "unavailable",
            "reason": outcome["reason"],
            "receipt_sha256": outcome["receipt_sha256"],
            "billed_cost": None,
        }, None
    envelope, receipt = retained
    summary = {
        "status": outcome["status"],
        "reason": outcome["reason"],
        "receipt_sha256": outcome["receipt_sha256"],
        "method_version_id": envelope["step"]["oci_method_version_id"],
        "parameter_sha256": envelope["step"]["parameters_sha256"],
        "wall_millis": receipt.wall_millis,
        "billed_cost": None,
    }
    return summary, outcome


def _classification(plan, scores):
    candidate, baseline = (scores.arms[name] for name in ("candidate", "baseline"))
    accuracy_gain = candidate.balanced_accuracy - baseline.balanced_accuracy
    added_harm = candidate.worst_class_error - baseline.worst_class_error
    gain_over_null = (
        candidate.balanced_accuracy
        - scores.negative_control.mean_balanced_accuracy.candidate
    )
    if (
        -accuracy_gain >= plan.minimum_balanced_accuracy_gain
        or added_harm > plan.maximum_worst_class_error_increase
    ):
        status = "worse_on_frozen_panel"
    elif min(accuracy_gain, gain_over_null) >= plan.minimum_balanced_accuracy_gain:
        status = "better_on_frozen_panel"
    else:
        status = "no_gain_established"
    return status, accuracy_gain, added_harm


def summarize_exploratory_benchmark(
    flow: AdaptiveWorkflow,
    executor: ExploratoryOCI,
    benchmark_id: str,
) -> dict[str, Any]:
    """Read a verified grouped score receipt without opening held measurements."""
    partition = _owner(flow, executor)
    with flow._read_connection() as db:
        plan, approval, approval_sha256 = _load_record(db, flow, benchmark_id)
        _require_partition(db, partition, plan, approval)
        state = flow._state(db)
        _assert_current(state, plan, approval)
        info = {
            name: _receipt(executor, state, step_id)
            for name, step_id in (
                ("candidate", plan.candidate.step_id),
                ("baseline", plan.baseline.step_id),
                ("evaluation", plan.evaluation_step_id),
            )
        }
        result = {
            "format": "devbench.exploratory-benchmark-result/1",
            "benchmark_id": benchmark_id,
            "approval_sha256": approval_sha256,
            "status": "unavailable",
            **_CLAIM,
            **{name: value[0] for name, value in info.items()},
        }
        evaluation = info["evaluation"][1]
        if (
            evaluation is None
            or evaluation["status"] != "succeeded"
            or not evaluation.get("accepted_record_id")
        ):
            return result
        token = state.dispatches[evaluation["attempt_id"]]["token"]
        if partition._status(db) != "consumed" or partition._consumption(db) != token:
            raise OperationBlocked("exploratory_benchmark_partition_unavailable")
        identity = evaluation["outputs"]["out"]["sha256"]
        with executor.evaluation.open(identity) as stream:
            content = stream.read(65_537)
        if len(content) > 65_536:
            raise ValueError("Grouped metrics exceed their disclosure budget")
        scores = _Scores.model_validate(decode_receipt_json(content))
        if scores.negative_control.seed != plan.null_seed:
            raise OperationConflict("Negative control differs from frozen seed")
        return _result(result, plan, scores, identity)


def _result(result, plan, scores, identity):
    status, gain, harm = _classification(plan, scores)
    for name, metrics in scores.arms.items():
        result[name]["metrics"] = metrics.model_dump(mode="json")
    return {
        **result,
        "status": status,
        "held_out_donors": scores.held_out_donors,
        "evaluated_classes": scores.class_count,
        "balanced_accuracy_gain": gain,
        "worst_class_error_increase": harm,
        "negative_control": scores.negative_control.model_dump(mode="json"),
        "evaluation_artifact_sha256": identity,
        "interpretation_scope": "observed_grouped_accuracy_on_this_frozen_panel",
    }
