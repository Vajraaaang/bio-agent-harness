"""Bound local Workbench controls and structural readback for thesis lineages."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import TypeAdapter

from devbench.analysis.thesis_synthesis import ThesisSynthesisSpec
from devbench.engine.exploratory_oci import ExploratoryOCI
from devbench.engine.partition_authority import embargoed
from devbench.engine.workflow_operations import OperationBlocked

from .adaptive_workflow_state import Identity
from .thesis_aims import AimDefinition, AimEvidence, AimOutcome, ExplanationAssessment
from .thesis_workflow import ThesisWorkflow
from .workbench_adaptive import _adaptive_ledger, _graph_revision, _readback_flow
from .workbench_thesis import (
    _check_graph_revision,
    _checked_thesis_lineage,
    _validate_expected_revision,
    _workspace_path,
    open_workbench_blinded_review,
    submit_workbench_blinded_review,
    write_workbench_thesis_package,
)

_ACTION_FIELDS = {
    "propose_aim": {"definition"},
    "revise_aim": {"definition", "rationale"},
    "approve_aim": {"aim_id", "rationale"},
    "assess_explanation": {"aim_id", "version", "assessment"},
    "open_blinded_review": {
        "aim_id",
        "version",
        "outcome",
        "masked_packet",
        "reviewers",
        "rationale",
    },
    "submit_blinded_review": {"aim_id", "version", "assessment_file"},
    "record_outcome": {"aim_id", "version", "outcome"},
    "synthesize": {"spec", "answer_schema"},
}
_NO_OWNER = "A current thesis coordinator is required for this lineage."
_NO_OPERATOR = "The current Workbench scientist is not an authorized thesis operator."
_NO_OUTCOMES = "The current evidence policy withholds research outcomes."
_NO_VERIFIER = "An independent review verifier is unavailable for this lineage."
_MAX_ANSWERABILITY = 40
_MAX_AIM_EVIDENCE = 16
_IDENTIFIER = TypeAdapter(Identity)
_REVIEWERS = TypeAdapter(tuple[Identity, ...])


def _capabilities(binding, lifecycle, flow, db):
    delivery = binding.delivery
    recipient, policy = delivery.broker._context_policy(db, delivery.context_id)
    actor = recipient["principal_id"]
    authorize = lifecycle.aims._authorize_operator if lifecycle is not None else None
    verifier = lifecycle.aims._verify_blinded_review if lifecycle is not None else None
    operator_reason = (
        _NO_OWNER
        if lifecycle is None
        else (
            None if callable(authorize) and authorize(actor) is True else _NO_OPERATOR
        )
    )
    review_reason = (
        _NO_OWNER
        if lifecycle is None
        else _NO_OUTCOMES
        if not policy.allow_outcomes
        else None
        if callable(verifier)
        else _NO_VERIFIER
    )
    assessment_reason = operator_reason or (
        None if policy.allow_outcomes else _NO_OUTCOMES
    )
    controller = flow.controller_state(connection=db)
    synthesis_reason = (
        assessment_reason
        or (
            "The current evidence policy withholds statistical export."
            if not policy.allow_statistics
            else None
        )
        or (
            "An artifact reader is unavailable for this lineage."
            if binding.tier_a_executor is None
            and type(binding.executor) is not ExploratoryOCI
            else None
        )
        or (
            "Synthesis requires a retained terminal controller outcome."
            if controller["binding"] is not None and controller["outcome"] is None
            else None
        )
    )
    reasons = {
        "propose_aim": operator_reason,
        "revise_aim": operator_reason,
        "approve_aim": operator_reason,
        "assess_explanation": assessment_reason,
        "open_blinded_review": operator_reason or review_reason,
        "submit_blinded_review": review_reason,
        "record_outcome": assessment_reason,
        "synthesize": synthesis_reason,
    }
    return (
        actor,
        policy,
        {
            name: {"enabled": reason is None, **({"reason": reason} if reason else {})}
            for name, reason in reasons.items()
        },
    )


def _public_panel(panel, db):
    # Only the aggregate panel state is eligible for readback.
    return {
        **{
            key: panel[key]
            for key in (
                "status",
                "reason",
                "subject_sha256",
                "masked_packet_sha256",
                "assigned_reviewers",
                "submitted_reviews",
                "unresolved_disagreement",
                "adjudicated",
                "veto_codes",
            )
            if key in panel
        },
        "receipt_sha256s": [
            digest for digest in panel["receipt_sha256s"] if not embargoed(db, digest)
        ],
    }


def _result_evidence(flow, db, state, step_id, result):
    receipt = result["receipt_sha256"]
    if result["status"] == "failed":
        yield AimEvidence(step_id=step_id, receipt_sha256=receipt)
    elif result["status"] == "succeeded":
        for port, output in result["outputs"].items():
            try:
                artifact = flow._current_use(db, step_id, port, "claim", None)
            except OperationBlocked:
                continue
            current_receipt = receipt
            if artifact.sha256 != output["sha256"]:
                from .thesis_joint_bh_release import current_joint_bh_release

                release, _ = current_joint_bh_release(
                    flow, db, state, result, claim=True
                )
                current_receipt = release.receipt_sha256()
            yield AimEvidence(
                step_id=step_id,
                receipt_sha256=current_receipt,
                artifact_sha256=artifact.sha256,
            )


def _verified_step_refs(flow, db, aim, context, step_id):
    from .thesis_aims import _verify_evidence

    state = context.adaptive
    result = state.outcome(step_id) if step_id in state.nodes else None
    if result is None:
        return
    for candidate in _result_evidence(flow, db, state, step_id, result):
        try:
            _verify_evidence(candidate, aim, context)
        except OperationBlocked:
            continue
        if not embargoed(db, candidate.receipt_sha256):
            yield candidate


def _evidence_refs(flow, db, aim, context, *, current_status):
    """List usable current references from this exact aim version, without bytes."""
    from .thesis_aims import _binding

    if current_status in {"invalidated", "operator_review", "blocked"}:
        return []
    basis = _binding(aim)
    cited = aim.outcome.evidence if aim.outcome is not None else ()
    refs = [
        ref
        for ref in dict.fromkeys(cited)
        if ref in _verified_step_refs(flow, db, aim, context, ref.step_id)
    ]
    for step_id, step in context.adaptive.nodes.items():
        if len(refs) == _MAX_AIM_EVIDENCE:
            break
        if basis.basis_id not in {item.basis_id for item in step.basis}:
            continue
        for candidate in _verified_step_refs(flow, db, aim, context, step_id):
            if candidate not in refs:
                refs.append(candidate)
            if len(refs) == _MAX_AIM_EVIDENCE:
                break
    return [ref.model_dump(mode="json", exclude_none=True) for ref in refs]


def _public_declaration(row, db):
    approval = row["approval"]
    if approval is None or row["status"] == "invalidated":
        return {}
    multiplicity = row.get("multiplicity")
    requirements = row.get("requirement_sources", ())
    identities = [
        approval["aim_sha256"],
        *row.get("input_sha256s", ()),
        *row.get("evaluation_sha256s", ()),
        *(item["source_sha256"] for item in requirements),
        *(item["value_sha256"] for item in requirements),
    ]
    if multiplicity is not None:
        identities.append(multiplicity["hypotheses_sha256"])
    if any(embargoed(db, value) for value in set(identities)):
        return {}
    return {
        "question": row["question"],
        "estimand": row["estimand"],
        "declaration": {
            key: row[key]
            for key in (
                "competing_explanations",
                "evaluation_sha256s",
                "input_sha256s",
                "multiplicity",
                "requirement_sources",
            )
            if key in row
        },
    }


def _public_aim(row, flow, db, *, inventory=None, evidence_context=None):
    allow_outcomes = inventory is not None
    status = row["status"]
    outcome = row["outcome"]
    if outcome is not None and not allow_outcomes and status != "invalidated":
        status = "outcome_withheld"
    stale_checks = row.get("stale_explanation_evidence", {})
    assessments = (
        {
            item["explanation_id"]: item["conclusion"]
            for item in row["assessed_explanations"]
        }
        if allow_outcomes
        else {}
    )
    explanations = []
    for item in row["competing_explanations"]:
        identity = item["explanation_id"]
        if allow_outcomes and identity in stale_checks:
            explanations.append(
                {
                    "explanation_id": identity,
                    "status": "stale",
                    "reason": stale_checks[identity],
                }
            )
        else:
            explanations.append(
                {
                    "explanation_id": identity,
                    "status": assessments.get(identity, "unassessed")
                    if allow_outcomes
                    else "withheld",
                }
            )
    view = {
        "aim_id": row["aim_id"],
        "version": row["version"],
        "kind": row["kind"],
        "status": status,
        "depends_on": row["depends_on"],
        "invalidated_by": row["invalidated_by"],
        "approved": row["approval"] is not None,
        "blinded_review_required": row.get("blinded_review_required", False),
        "competing_explanations": explanations,
        **({"reason": row["reason"]} if "reason" in row else {}),
    }
    if not allow_outcomes:
        return view
    evidence = _evidence_refs(
        flow,
        db,
        inventory.aims[row["aim_id"]],
        evidence_context,
        current_status=status,
    )
    view["evidence"] = evidence
    declaration = _public_declaration(row, db)
    if declaration:
        view.update(declaration)
        statements = {
            item["explanation_id"]: item["explanation"]
            for item in declaration["declaration"]["competing_explanations"]
        }
        cited_assessments = {
            item["explanation_id"]: {
                key: value for key, value in item["evidence"].items()
                if value is not None
            }
            for item in row["assessed_explanations"]
        }
        for item in explanations:
            identity = item["explanation_id"]
            item["explanation"] = statements[identity]
            cited = cited_assessments.get(identity)
            if cited is not None and identity not in stale_checks and cited in evidence:
                item["evidence"] = cited
    if outcome is not None and status != "invalidated":
        cited = [
            {key: value for key, value in item.items() if value is not None}
            for item in outcome["evidence"]
        ]
        view["outcome"] = {
            "status": outcome["status"],
            "evidence": [ref for ref in evidence if ref in cited],
        }
    panel = row.get("blinded_review")
    if panel is not None and not (
        embargoed(db, panel["subject_sha256"])
        or embargoed(db, panel["masked_packet_sha256"])
    ):
        view["blinded_review"] = _public_panel(panel, db)
    return view


def thesis_view(app, session_id: str) -> dict[str, Any]:
    """Deliver current, compact aim and decision states to the bound scientist."""
    from .workbench_interactive import ensure_adaptive_binding
    from .workbench_thesis_export import thesis_export_inventory

    scientific = app.scientific
    if scientific is None:
        raise PermissionError("Enable the scientific workspace for thesis workflows")
    scientific._plan(session_id)
    base = {
        "session_id": session_id,
        "status": "unavailable",
        "clinical_release_allowed": False,
        "raw_matrix_rows": 0,
        "aims": [],
        "answerability": [],
        "actions": {
            name: {"enabled": False, "reason": _NO_OWNER} for name in _ACTION_FIELDS
        },
    }
    binding = ensure_adaptive_binding(app, session_id)
    if binding is None:
        if _adaptive_ledger(scientific, session_id) is not None:
            base["status"] = "current_delivery_required"
        return base
    _, ledger = binding.require_owner(scientific)
    flow = _readback_flow(scientific, binding, ledger)
    lifecycle = binding.thesis_lifecycle or getattr(
        binding.controller, "thesis_lifecycle", None
    )
    retained = (
        lifecycle if lifecycle is not None else ThesisWorkflow(ledger, session_id)
    )
    with flow._read_connection() as db:
        revision = _graph_revision(flow, connection=db)
        _, policy, capabilities = _capabilities(binding, lifecycle, flow, db)
        context = retained.scientific_context(connection=db)
        rows = context["research_aims"]
        proofs = context["answerability"]
        inventory, evidence_context = None, None
        if rows and policy.allow_outcomes:
            inventory, _ = retained.aims._projection(db)
            evidence_context = retained.aims._evidence_context(db)
        result = {
            **base,
            "status": "available",
            "revision": revision,
            "aims": [
                _public_aim(
                    row,
                    flow,
                    db,
                    inventory=inventory,
                    evidence_context=evidence_context,
                )
                for row in rows
            ],
            "answerability": [
                {
                    key: row[key]
                    for key in (
                        "aim_id",
                        "aim_version",
                        "step_id",
                        "proof_sha256",
                        "status",
                        "reason",
                        "missing_measurements",
                        "execution_route",
                    )
                }
                for row in proofs[-_MAX_ANSWERABILITY:]
                if not embargoed(db, row["proof_sha256"])
            ],
            "actions": capabilities,
            "packages": (
                thesis_export_inventory(
                    app, session_id, revision,
                    allow_export=capabilities["synthesize"]["enabled"],
                )
                if policy.allow_outcomes and policy.allow_statistics
                else []
            ),
        }
    delivery = binding.delivery

    def current(db):
        _check_graph_revision(flow, revision, connection=db)

    reservation = delivery.broker.reserve(
        delivery.context_id,
        delivery.source_id,
        result,
        _guard=current,
    )
    delivery.broker.mark_attempted(reservation["reservation_id"], _guard=current)
    return result


def _aim_action(aims, name, request, actor, kwargs):
    if name in {"propose_aim", "revise_aim"}:
        definition = AimDefinition.model_validate(request["definition"])
        if name == "propose_aim":
            aims.propose(definition, **kwargs)
        else:
            aims.revise(
                definition, actor=actor, rationale=request["rationale"], **kwargs
            )
        return
    aim_id = request["aim_id"]
    if name == "approve_aim":
        aims.approve(aim_id, actor=actor, rationale=request["rationale"], **kwargs)
    elif name == "assess_explanation":
        aims.assess_explanation(
            aim_id,
            ExplanationAssessment.model_validate(request["assessment"]),
            version=request["version"],
            **kwargs,
        )
    elif name == "record_outcome":
        outcome = AimOutcome.model_validate(request["outcome"])
        aims.record_outcome(
            aim_id,
            version=request["version"],
            **outcome.model_dump(mode="json"),
            **kwargs,
        )


def _local_path(value):
    if type(value) is not str or not value or len(value) > 4096 or "\x00" in value:
        raise ValueError("Use a bounded absolute path in the current Workbench project")
    return Path(value)


def _synthesize(app, session_id, flow, request, ledger_revision):
    from .workbench_thesis_export import record_thesis_export

    values = request["spec"]
    if type(values) is not dict or "expected_revision" in values:
        raise ValueError(
            "The thesis synthesis specification uses the bound ledger revision"
        )
    schema = _workspace_path(
        _local_path(request["answer_schema"]), (app.workspace, app.scientific.root)
    )
    spec = ThesisSynthesisSpec.model_validate(
        {**values, "expected_revision": ledger_revision}
    )
    package_id = uuid4().hex
    destination = app.workspace / "thesis-packages" / session_id / package_id
    _workspace_path(destination, (app.workspace,))
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    package = write_workbench_thesis_package(
        app,
        session_id,
        spec,
        root=destination,
        answer_schema=schema,
    )
    revision = request["expected_revision"]
    record_thesis_export(app, session_id, package_id, package, revision)
    _check_graph_revision(flow, revision)
    return {
        "revision": revision,
        "package": {**package, "directory": str(destination), "package_id": package_id},
    }


def _review_action(app, session_id, name, request, actor, revision):
    context = {
        "aim_id": request["aim_id"],
        "version": request["version"],
        "expected_revision": revision,
    }
    if name == "submit_blinded_review":
        return submit_workbench_blinded_review(
            app,
            session_id,
            assessment_file=_local_path(request["assessment_file"]),
            **context,
        )
    return open_workbench_blinded_review(
        app,
        session_id,
        outcome=AimOutcome.model_validate(request["outcome"]),
        masked_packet=_local_path(request["masked_packet"]),
        reviewers=_REVIEWERS.validate_python(request["reviewers"]),
        actor=actor,
        rationale=request["rationale"],
        **context,
    )


def thesis_action(app, session_id: str, request: dict[str, Any]) -> dict[str, Any]:
    """Apply one browser action with the current coordinator's own authority."""
    name = request.get("action") if type(request) is dict else None
    if (
        type(name) is not str
        or name not in _ACTION_FIELDS
        or set(request)
        != {
            "action",
            "expected_revision",
            *_ACTION_FIELDS[name],
        }
    ):
        raise ValueError("Use only the declared fields for a thesis action")
    revision = request["expected_revision"]
    _validate_expected_revision(revision)
    if "aim_id" in request:
        _IDENTIFIER.validate_python(request["aim_id"])
    if "version" in request and (
        type(request["version"]) is not int or request["version"] < 1
    ):
        raise ValueError("Use the current positive aim version")
    with app._lock:
        flow, lifecycle, binding = _checked_thesis_lineage(app, session_id)
        review_intake = name in {"open_blinded_review", "submit_blinded_review"}
        external_step = review_intake or name == "synthesize"
        transaction = (
            flow._read_connection()
            if external_step
            else (flow.authority._execution_tx(session_id))
        )
        with transaction as db:
            _check_graph_revision(flow, revision, connection=db)
            actor, _, capabilities = _capabilities(binding, lifecycle, flow, db)
            if not capabilities[name]["enabled"]:
                raise PermissionError(capabilities[name]["reason"])
            ledger_revision = flow.authority._revision(db, session_id)
            if not external_step:
                _aim_action(
                    lifecycle.aims,
                    name,
                    request,
                    actor,
                    {"connection": db, "expected_revision": ledger_revision},
                )
                return {"revision": _graph_revision(flow, connection=db)}
        if review_intake:
            return _review_action(app, session_id, name, request, actor, revision)
        return _synthesize(app, session_id, flow, request, ledger_revision)
