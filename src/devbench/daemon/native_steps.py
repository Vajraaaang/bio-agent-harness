"""Resume curated scientific steps only from current, independently checked evidence."""

import re

from devbench.engine.workflow_operations import OperationBlocked, OperationConflict
from devbench.platform.adaptive_workflow import request_digest


def _mismatch():
    return OperationBlocked("native_step_checkpoint_mismatch")


def _valid_prefix(entries, plan_id):
    if not isinstance(entries, list) or len(entries) > 16:
        return False
    for index, entry in enumerate(entries, 1):
        if (
            not isinstance(entry, dict)
            or set(entry)
            != {"step_id", "attempt_id", "receipt_sha256", "outputs_sha256"}
            or entry["step_id"] != f"plugin-step-{plan_id}-{index}"
            or not all(isinstance(value, str) for value in entry.values())
            or not re.fullmatch(r"[0-9a-f]{32}", entry["attempt_id"])
            or not re.fullmatch(r"[0-9a-f]{64}", entry["receipt_sha256"])
            or not re.fullmatch(r"[0-9a-f]{64}", entry["outputs_sha256"])
        ):
            return False
    return True


def _snapshot(flow, executor, db, step_id, actor):
    state = flow._state(db)
    outcome = state.outcome(step_id)
    if (
        not outcome
        or outcome["status"] != "succeeded"
        or not outcome["accepted_record_id"]
    ):
        raise _mismatch()
    dispatch = state.dispatches[outcome["attempt_id"]]
    envelope, token = dispatch["envelope"], dispatch["token"]
    authority = flow.authority
    authority._active(
        db, flow.lineage_id, authority._load_state(db, flow.lineage_id), token
    )
    attempt = authority._checked_attempt(db, token)
    if (
        attempt["acceptance_state"] != "accepted"
        or attempt["execution_state"] != "succeeded"
        or attempt["cleanup_state"] != "confirmed"
        or attempt["output_record_id"] != outcome["accepted_record_id"]
        or attempt["verification_sha256"] != outcome["receipt_sha256"]
        or attempt["artifact_sha256"] != request_digest(outcome["outputs"])
        or token["backend_id"] != executor.backend_id
        or token["environment_sha256"] != executor.environment_sha256
        or (actor is not None and envelope["actor"] != actor)
        or flow._envelope(state, step_id, envelope["actor"], token=token, db=db)
        != envelope
    ):
        raise _mismatch()
    for port, artifact in outcome["outputs"].items():
        current = flow._current_use(
            db, step_id, port, "compute", envelope["generation"]
        )
        if current.model_dump(mode="json") != artifact:
            raise _mismatch()
    return outcome, envelope


def accepted_step(flow, executor, step_id, *, expected=None, actor=None):
    """Verify the WAL grant, output versions and original Tier A receipt/artifacts."""

    def read(db):
        return _snapshot(flow, executor, db, step_id, actor)

    outcome, envelope = flow._read_projection(read)
    receipt = executor.load_receipt(envelope, attempt_id=outcome["attempt_id"])
    if (
        receipt is None
        or request_digest(receipt.model_dump(mode="json")) != outcome["receipt_sha256"]
    ):
        raise _mismatch()
    entry = {
        "step_id": step_id,
        "attempt_id": outcome["attempt_id"],
        "receipt_sha256": outcome["receipt_sha256"],
        "outputs_sha256": request_digest(outcome["outputs"]),
    }
    if expected is not None and expected != entry:
        raise _mismatch()
    # The file read above can outlive an operator hold or epoch change.
    if flow._read_projection(read) != (outcome, envelope):
        raise _mismatch()
    return outcome, entry


def _check_registration(flow, executor, db, state, request):
    from devbench.core._hashing import canonical_sha256
    from devbench.engine.tier_a_scrna import build_checked_step
    from devbench.platform.adaptive_workflow_state import BasisUse
    from devbench.platform.workbench_curated_actions import (
        _REVIEWED_INPUTS,
        _current_source,
    )
    from rna_kernels.scrna_upstream_contract import SingleCellStepSpec

    sources, basis = {}, []
    for port, link in request["sources"].items():
        sources[port], artifact = _current_source(flow, db, state, link)
        if port in _REVIEWED_INPUTS:
            identity = next(
                (
                    key
                    for key, item in sorted(state.basis.items())
                    if item["kind"] == "review_basis"
                    and item["sha256"] == artifact.sha256
                ),
                None,
            )
            if identity is None:
                raise _mismatch()
            basis.append(BasisUse(basis_id=identity))
    spec = SingleCellStepSpec.model_validate(
        {"operation": request["operation"], "parameters": request["parameters"]}
    )
    step = build_checked_step(
        executor,
        spec.operation,
        canonical_sha256(spec.model_dump(mode="json")),
        step_id=request["step_id"],
        branch_id=request["branch_id"],
        biological_unit=request["biological_unit"],
        sources=sources,
        basis=tuple(basis),
    )
    if state.nodes[step.step_id] != step:
        raise _mismatch()


class NativeSteps:
    """Keep an ordered cursor for one frozen Tier A plan and logical run ID."""

    def __init__(self, jobs, rid, flow, executor, plan_id, *, actor, enabled=True):
        self.jobs, self.rid, self.flow, self.executor = jobs, rid, flow, executor
        self.actor = actor
        self.enabled = enabled and callable(getattr(jobs, "checkpoint", None))
        self.entries = []
        self.restoring = False
        if not self.enabled:
            return
        row = jobs.run(flow.lineage_id, rid)
        if row["plan_id"] != plan_id:
            raise _mismatch()
        self.binding = {
            "worker_protocol": "devbench.plugin-worker/1",
            "phase": "native_steps",
            "lineage_id": flow.lineage_id,
            "plan_id": plan_id,
            "plan_sha256": row["plan_sha256"],
        }
        checkpoint = jobs.resume_checkpoint(rid)
        if (
            checkpoint is not None
            and checkpoint["state"].get("phase") == "native_steps"
        ):
            state = checkpoint["state"]
            if (
                state.keys() != self.binding.keys() | {"steps"}
                or any(state[key] != value for key, value in self.binding.items())
                or not _valid_prefix(state["steps"], plan_id)
            ):
                raise _mismatch()
            self.entries, self.restoring = list(state["steps"]), True
        else:
            self._save()

    def _save(self):
        self.jobs.checkpoint(
            self.rid,
            step="single_cell_steps",
            state={**self.binding, "steps": self.entries},
        )

    def prepare(self, request, index):
        """Register new work or recover an accepted prefix without dispatching it."""
        from devbench.platform.workbench_curated_actions import _queue

        step_id = request["step_id"]
        with self.flow.authority._execution_tx(self.flow.lineage_id) as db:
            state = self.flow._state(db)
            if step_id not in state.nodes:
                if index <= len(self.entries):
                    raise _mismatch()
                _queue(self.flow, self.executor, db, state, request)
                return None
            if not self.restoring:
                raise OperationConflict("This workflow step ID is already used")
            _check_registration(self.flow, self.executor, db, state, request)
            if state.outcome(step_id) is None:
                dispatched = any(
                    item["envelope"]["step"]["step_id"] == step_id
                    for item in state.dispatches.values()
                )
                if dispatched or index <= len(self.entries):
                    raise OperationBlocked("native_step_requires_reconciliation")
                return None
        return self.accept(step_id, index)

    def accept(self, step_id, index):
        known = index <= len(self.entries)
        expected = self.entries[index - 1] if known else None
        outcome, entry = accepted_step(
            self.flow, self.executor, step_id, expected=expected, actor=self.actor
        )
        if not known:
            if index != len(self.entries) + 1:
                raise _mismatch()
            self.entries.append(entry)
            self._save()
        return outcome
