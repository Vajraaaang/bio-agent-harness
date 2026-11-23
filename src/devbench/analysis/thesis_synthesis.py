"""Seal a thesis package from one current adaptive scientific ledger revision."""

from __future__ import annotations

import hashlib
import re
import shutil
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from jsonschema.exceptions import ValidationError
from pydantic import Field

from devbench.adaptive.models import AdaptiveRecord
from devbench.core._files import hash_bounded_file, read_bounded_file
from devbench.core._hashing import decode_receipt_json
from devbench.core.contrast_design import TIER_A_BULK_OPERATION, TIER_A_BULK_OUTPUT
from devbench.core.joint_bh import JOINT_BH_INPUT, JOINT_BH_OUTPUT_PREFIX
from devbench.engine.permissions import require_protected_storage
from devbench.engine.workflow_operations import OperationBlocked, OperationConflict
from devbench.platform.adaptive_workflow_state import Digest, Identity
from rna_kernels.canonical import canonical_json

from .thesis_literature import (
    ThesisLiteratureComparison,
    project_literature_comparison,
    render_literature_comparison,
)
from .thesis_synthesis_evidence import (
    MAX_ANSWER_BYTES,
    MAX_ARTIFACT_BYTES,
    MAX_SCHEMA_BYTES,
    AnswerFieldRef,
    FigureRef,
    OpenArtifact,
    VerifiedField,
    extract_field,
    negative_confirmatory_aims,
    read_figure_pair,
    read_negative_families,
    validate_answer,
    validate_gene_fields,
    validate_negative_aims,
)

if TYPE_CHECKING:
    from sqlite3 import Connection

    from devbench.engine.exploratory_oci import ExploratoryOCI
    from devbench.engine.tier_a_executor import TierAExecutor
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    from devbench.platform.thesis_aims import ThesisAimLifecycle

THESIS_RECEIPT = "thesis-package-receipt.json"
FORMAT = "devbench.adaptive-thesis/1"
_PACKAGED = re.compile(
    r"[A-Za-z][A-Za-z0-9_.-]{0,127}(?:/[A-Za-z0-9][A-Za-z0-9_.-]{0,127}){0,3}",
)
_DIGEST = re.compile(r"[a-f0-9]{64}")
_MAX_FILES = 512
_MAX_METADATA = 8_000_000
_MAX_PACKAGE_BYTES = 128_000_000
_MAX_FIGURE_BYTES = 64_000_000
_INFERENCE = frozenset({"admitted_research_inference", "qualified_inference"})
Note = Annotated[str, Field(min_length=1, max_length=2000)]


class RequirementSource(AdaptiveRecord):
    requirement_id: Identity
    description: Note
    origin: Literal[
        "user_prespecified",
        "assistant_proposed",
        "fixture_default",
        "operator_approved",
    ]
    source_sha256: Digest


class FollowUp(AdaptiveRecord):
    experiment: Note
    uncertainty: Note
    observable: Note


class ThesisSynthesisSpec(AdaptiveRecord):
    question: Note
    outcome: Literal["completed", "partial", "refused", "abstained"]
    expected_revision: Annotated[int, Field(gt=0, strict=True)]
    answer_schema_sha256: Digest
    fields: dict[Identity, AnswerFieldRef] = Field(default_factory=dict, max_length=64)
    primary_branch_id: Identity | None = None
    data_notes: dict[
        Literal["observations", "declarations", "inferences", "unknowns"],
        tuple[Note, ...],
    ] = Field(default_factory=dict)
    requirement_sources: tuple[RequirementSource, ...] = Field(
        default=(), max_length=64
    )
    follow_up: tuple[FollowUp, ...] = Field(default=(), max_length=20)
    figures: tuple[FigureRef, ...] = Field(default=(), max_length=20)
    literature: ThesisLiteratureComparison | None = None
    discovery_threshold: Annotated[float, Field(gt=0, lt=1, strict=True)] | None = None


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_path(path: Path) -> Path:
    if not path.is_absolute() or path != path.resolve():
        raise ValueError("Thesis package paths must be canonical and absolute")
    return path


def _resolve_output(
    workflow: AdaptiveWorkflow,
    db: Connection,
    state,
    step_id: str,
    port: str | None,
) -> VerifiedField:
    step = state.nodes.get(step_id)
    if step is None:
        raise OperationBlocked("thesis_current_receipt_unavailable")
    result = state.outcome(step_id)
    if result is None:
        raise OperationBlocked("thesis_current_receipt_unavailable")
    if port is None:
        if result["status"] != "failed" or not result["reason"]:
            raise OperationBlocked("thesis_verified_refusal_unavailable")
        artifact = None
    else:
        artifact = workflow._current_use(db, step_id, port, "claim", None)
    receipt_sha256 = result["receipt_sha256"]
    if artifact is not None and artifact.version_id.startswith(JOINT_BH_OUTPUT_PREFIX):
        from devbench.platform.thesis_joint_bh_release import current_joint_bh_release

        release, _ = current_joint_bh_release(workflow, db, state, result, claim=False)
        receipt_sha256 = release.receipt_sha256()
    return VerifiedField(
        step_id=step_id,
        branch_id=step.branch_id,
        generation=state.generations[step_id],
        receipt_sha256=receipt_sha256,
        attempt_id=result["attempt_id"],
        artifact=artifact,
        claim_scope=step.purpose,
        failure_reason=result["reason"],
    )


def _required_source_records(spec: ThesisSynthesisSpec, state) -> None:
    facts = {item["sha256"] for item in state.basis.values() if item["kind"] == "fact"}
    for source in spec.requirement_sources:
        if source.origin != "user_prespecified":
            continue
        exact = canonical_json(
            source.model_dump(mode="json", exclude={"source_sha256"})
        )
        if source.source_sha256 not in facts or _sha(exact) != source.source_sha256:
            raise ValueError(
                "User-prespecified requirements need a retained fact record"
            )


def _resolve_fields(workflow, db, state, spec):
    bindings, unavailable = {}, {}
    for name, reference in sorted(spec.fields.items()):
        try:
            binding = _resolve_output(
                workflow, db, state, reference.step_id, reference.port
            )
        except OperationBlocked as error:
            if spec.outcome == "completed":
                raise
            unavailable[name] = error.state
        else:
            step = state.nodes[reference.step_id]
            if (
                name == "gene_statistics"
                and (
                    (step.comparison and not step.primary)
                    or (spec.primary_branch_id is not None
                        and binding.branch_id != spec.primary_branch_id)
                )
            ):
                raise ValueError(
                    "Primary gene statistics must bind the declared primary branch"
                )
            bindings[name] = binding
    return bindings, unavailable


def _resolve_figures(workflow, db, state, spec):
    resolved, unavailable = [], []
    for index, figure in enumerate(spec.figures):
        try:
            image, numeric, report = (
                _resolve_output(workflow, db, state, figure.step_id, port)
                for port in (
                    figure.figure_port,
                    figure.numeric_port,
                    figure.report_port,
                )
            )
        except OperationBlocked as error:
            unavailable.append({"figure": index + 1, "reason": error.state})
        else:
            resolved.append((index + 1, figure, image, numeric, report))
    return resolved, unavailable


def _inputs(state) -> list[dict[str, Any]]:
    active = {
        pin["version_id"]
        for record in state.dispatches.values()
        for pin in record["envelope"]["pins"]
    }
    return [
        item
        for identity, (item, producer) in sorted(state.artifacts.items())
        if producer is None and identity in active
    ]


def _method_steps(state) -> list[dict[str, Any]]:
    return [
        {
            "step_id": step.step_id,
            "branch_id": step.branch_id,
            "operation_id": step.operation_id,
            "purpose": step.purpose,
            "profile_id": step.profile.profile_id,
            "version": step.profile.version,
            "parameters_sha256": step.parameters_sha256,
            "environment_sha256": step.environment_sha256,
            "status": (state.outcome(step.step_id) or {}).get("status", "planned"),
        }
        for step in sorted(state.nodes.values(), key=lambda item: item.step_id)
    ]


def _aims_in_snapshot(aim_lifecycle, workflow, db):
    from devbench.platform.thesis_aims import ThesisAimLifecycle

    supplied = aim_lifecycle is not None
    if not supplied:
        aim_lifecycle = ThesisAimLifecycle(workflow.ledger, workflow.lineage_id)
    if (
        aim_lifecycle.ledger.path != workflow.ledger.path
        or aim_lifecycle.lineage_id != workflow.lineage_id
    ):
        raise PermissionError("Thesis aims belong to another scientific lineage")
    context = aim_lifecycle.scientific_context(connection=db)
    if context.get("raw_matrix_rows") != 0:
        raise ValueError("Thesis aims may only expose compact receipt metadata")
    if not supplied and not context["research_aims"]:
        return {"research_aims": [], "raw_matrix_rows": 0}
    return {
        **context,
        "revision_history": aim_lifecycle.history(
            connection=db, include_blinded_audit=False
        ),
    }


def _check_completed_aims(spec: ThesisSynthesisSpec, aims: dict[str, Any]) -> None:
    if spec.outcome != "completed":
        return
    unclosed = [
        row
        for row in aims["research_aims"]
        if row["status"] not in {"supported", "no_significant_finding", "refused"}
    ]
    if any(row.get("blinded_review_required") for row in unclosed):
        raise OperationBlocked("thesis_required_review_aim_not_closed")
    approved = {
        event["aim_id"]
        for event in aims.get("revision_history", ())
        if event["action"] == "approve"
    }
    if any(
        row.get("approval") is not None or row["aim_id"] in approved
        for row in unclosed
    ):
        raise OperationBlocked("thesis_approved_aim_not_closed")


def _joint_publications(
    workflow: AdaptiveWorkflow,
    db: Connection,
    state,
    fields: Iterable[VerifiedField],
    discovery_threshold: float | None,
) -> dict[str, Any]:
    releases = {}
    for field in fields:
        artifact = field.artifact
        if artifact is None or not artifact.version_id.startswith(
            JOINT_BH_OUTPUT_PREFIX
        ):
            continue
        from devbench.platform.thesis_joint_bh_release import current_joint_bh_release

        release, _ = current_joint_bh_release(
            workflow, db, state, state.outcome(field.step_id), claim=False
        )
        if discovery_threshold is not None and discovery_threshold != release.fdr:
            raise ValueError(
                "Thesis discovery threshold differs from the frozen family"
            )
        releases[release.receipt_sha256()] = release.model_dump(mode="json")
    return releases


def _negative_primary_fields(
    workflow: AdaptiveWorkflow, db: Connection, state, aims: dict[str, Any]
) -> dict[str, list[VerifiedField]]:
    fields: dict[str, list[VerifiedField]] = {}
    for aim in negative_confirmatory_aims(aims):
        for citation in aim["outcome"]["evidence"]:
            binding = _cited_bulk_result(
                workflow, db, state, citation,
                representations={"parquet", JOINT_BH_INPUT}, primary_only=True,
            )
            if binding is not None:
                fields.setdefault(aim["aim_id"], []).append(binding)
    return fields


def _cited_bulk_result(
    workflow: AdaptiveWorkflow,
    db: Connection,
    state,
    citation: dict[str, Any],
    *,
    representations: set[str],
    primary_only: bool = False,
) -> VerifiedField | None:
    step = state.nodes.get(citation["step_id"])
    if (
        citation.get("artifact_sha256") is None
        or step is None
        or step.operation_id != TIER_A_BULK_OPERATION
        or (primary_only and not step.primary)
    ):
        return None
    result = state.outcome(step.step_id)
    raw = result["outputs"].get(TIER_A_BULK_OUTPUT) if result is not None else None
    if raw is None or raw["representation"] not in representations:
        return None
    binding = _resolve_output(workflow, db, state, step.step_id, TIER_A_BULK_OUTPUT)
    return binding if binding.source_identity() == citation else None


def _current_aim_citations(aims: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for aim in aims["research_aims"]:
        if aim["status"] == "invalidated":
            continue
        outcome = aim.get("outcome")
        if outcome and aim["status"] == outcome["status"]:
            yield from outcome["evidence"]
        stale = aim.get("stale_explanation_evidence", {})
        for assessment in aim["assessed_explanations"]:
            if assessment["explanation_id"] not in stale:
                yield assessment["evidence"]


def _cited_joint_fields(
    workflow: AdaptiveWorkflow,
    db: Connection,
    state,
    aims: dict[str, Any],
    seen_receipts: Iterable[str],
) -> Iterator[VerifiedField]:
    seen = set(seen_receipts)
    for citation in _current_aim_citations(aims):
        receipt = citation["receipt_sha256"]
        if receipt in seen:
            continue
        binding = _cited_bulk_result(
            workflow, db, state, citation, representations={JOINT_BH_INPUT}
        )
        if binding is not None:
            seen.add(receipt)
            yield binding


def _snapshot(workflow, spec, aim_lifecycle):
    def read(db):
        revision = workflow.authority._revision(db, workflow.lineage_id)
        if revision != spec.expected_revision:
            raise OperationConflict(
                "Thesis synthesis requires its current ledger revision"
            )
        state, head = workflow._projection(db)
        if (
            state.controller["binding"] is not None
            and state.controller["outcome"] is None
        ):
            raise OperationBlocked("thesis_requires_terminal_controller_outcome")
        _required_source_records(spec, state)
        fields, missing = _resolve_fields(workflow, db, state, spec)
        figures, absent = _resolve_figures(workflow, db, state, spec)
        dag = workflow.projection_snapshot(connection=db)
        comparison = spec.literature
        literature = (
            None
            if comparison is None
            else project_literature_comparison(
                workflow,
                db,
                comparison,
                basis=state.basis.get(comparison.plan_basis_id),
            )
        )
        aims = _aims_in_snapshot(aim_lifecycle, workflow, db)
        _check_completed_aims(spec, aims)
        negative_fields = _negative_primary_fields(workflow, db, state, aims)
        publications = _joint_publications(
            workflow, db, state, fields.values(), spec.discovery_threshold
        )
        cited_only = _cited_joint_fields(workflow, db, state, aims, publications)
        publications.update(_joint_publications(workflow, db, state, cited_only, None))
        return {
            "lineage_id": workflow.lineage_id,
            "revision": revision,
            "adaptive_root_sha256": head.records_sha256,
            "dag": dag,
            "aims": aims,
            "inputs": _inputs(state),
            "methods": _method_steps(state),
            "fields": fields,
            "negative_primary_fields": negative_fields,
            "joint_publications": publications,
            "missing": missing,
            "figures": figures,
            "absent": absent,
            "literature": literature,
        }

    return workflow._read_projection(read)


def _design_summary(
    spec: ThesisSynthesisSpec, snapshot: dict[str, Any]
) -> dict[str, Any]:
    notes = spec.model_dump(mode="json")["data_notes"]
    if sum(map(len, notes.values())) > 128:
        raise ValueError("Thesis data notes exceed their bounded inventory")
    return {
        "notes": notes,
        "notes_origin": "supplied_synthesis_declarations",
        "observed_inputs": snapshot["inputs"],
        "biological_units": sorted(
            {row["biological_unit"] for row in snapshot["inputs"]}
        ),
    }


def _assemble_answer(
    spec: ThesisSynthesisSpec,
    schema: Any,
    snapshot: dict[str, Any],
    open_artifact: OpenArtifact,
) -> tuple[dict[str, Any], bytes | None, dict[str, Any]]:
    answer: dict[str, Any] = {}
    for key, binding in snapshot["fields"].items():
        value = extract_field(spec.fields[key], binding, open_artifact)
        validate_gene_fields(
            {key: value}, inferential_scope=binding.claim_scope in _INFERENCE
        )
        answer[key] = value
    try:
        if not answer:
            raise ValidationError("No current receipt supports an answer field")
        validate_answer(schema, answer)
        raw = canonical_json(answer)
        if len(raw) > MAX_ANSWER_BYTES:
            raise ValidationError("The complete answer exceeds the declared byte limit")
    except ValidationError as error:
        if spec.outcome == "completed":
            raise ValueError(error.message) from error
        return {}, None, {"status": "withheld", "reason": error.message}
    return answer, raw, {"status": "validated", "sha256": _sha(raw)}


def _figure_files(snapshot, open_artifact):
    files, records = {}, []
    used = 0
    for number, reference, image, numeric, report in snapshot["figures"]:
        image_raw, data_raw, state = read_figure_pair(
            reference,
            figure=image.artifact,
            numeric=numeric.artifact,
            report=report.artifact,
            opener=open_artifact,
        )
        image_file = f"figures/figure-{number:02d}{reference.figure_suffix}"
        data_file = f"figures/figure-{number:02d}{reference.numeric_suffix}"
        used += len(image_raw) + len(data_raw)
        if used > _MAX_FIGURE_BYTES:
            raise ValueError(
                "Thesis figure and sidecar inventory exceeds its byte bound"
            )
        files[image_file], files[data_file] = image_raw, data_raw
        records.append(
            {
                "image": image_file,
                "numeric_sidecar": data_file,
                "plot_state": state,
                "step_id": reference.step_id,
                "receipt_sha256": image.receipt_sha256,
                "report_sha256": report.artifact.sha256,
            }
        )
    return files, records


def _render_methods(methods: list[dict[str, Any]], runtime: dict[str, Any]) -> bytes:
    from .episode_synthesis import _text

    lines = ["# Versioned methods", ""]
    for item in methods:
        lines.extend(
            (
                f"- {_text(item['step_id'])} recorded operation "
                f"`{item['operation_id']}` under profile "
                f"`{item['profile_id']}` version {item['version']} "
                f"with scope `{item['purpose']}` and status `{item['status']}`.",
                f"  Parameters `{item['parameters_sha256']}` and environment "
                f"`{item['environment_sha256']}`.",
            )
        )
    if not methods:
        lines.append("No registered methods were retained.")
    for method in runtime["observed_methods"]:
        lines.append("")
        step = _text(method["step_id"])
        details = ", ".join(
            f"{_text(key.replace('_', ' '))} {_text(value)}"
            for key, value in sorted(method["observed"].items())
        )
        lines.append(f"Observed method details for {step} included {details}.")
    lines.extend(_render_replay_methods(runtime))
    return ("\n".join(lines) + "\n").encode()


def _render_replay_methods(runtime: dict[str, Any]) -> list[str]:
    lines = [
        "",
        "## Replay",
        "",
        "Run `replay_bundle/replay.py` with "
        "`--expected-root` to verify the package.",
    ]
    if runtime["numerical_replay_available"]:
        fits = "Tier A" if runtime.get("compact_fit_replay") else "mounted"
        stages = (
            "stages"
            if runtime.get("single_cell_replay_available")
            or runtime.get("exploratory_replay_available")
            else "fits"
        )
        lines.append(
            f"For the retained {fits} {stages}, also pass `--input-root` with "
            "the local digest-keyed inputs, `--output` with a fresh directory, and "
            "`--allow-data-execution`. The script verifies exact result bytes."
        )
        if runtime.get("compact_fit_replay"):
            lines.append("Compact fit replay requires the recorded host runtime.")
    elif runtime.get("cross_aim_bh_replay") != "unavailable":
        lines.append("Numerical replay was unavailable for the retained methods.")
    if runtime.get("single_cell_replay_omissions"):
        lines.append("The provenance manifest records unavailable single-cell stages.")
    if runtime.get("exploratory_replay_available"):
        lines.append(
            "Tier B replay also requires `--tier-b-owner` with the current local "
            "method registry and ledger."
        )
    if runtime.get("exploratory_replay_omissions"):
        lines.append("The provenance manifest records unavailable Tier B stages.")
    if runtime.get("cross_aim_bh_replay") == "unavailable":
        lines.append("Numerical replay of the cross-aim BH tables remains unavailable.")
    elif runtime.get("cross_aim_bh_replay") in {"available", "partial"}:
        lines.append(
            "The replay index identifies each pooled BH family covered. Replay checks "
            "the exact bytes of all its source fits and adjusted Parquet tables."
        )
    return lines


def _show_data(manifest) -> Iterator[str]:
    from .episode_synthesis import _text

    data = manifest["data_inventory"]
    for category in ("observations", "declarations", "inferences", "unknowns"):
        for note in data["notes"].get(category, []):
            yield f"- Supplied notes on {category} included {_text(note)}."
    if data["observed_inputs"]:
        count = len(data["observed_inputs"])
        yield f"The ledger retained {count} used source artifacts."
        for kind in data["biological_units"]:
            yield f"- {_text(kind)} was a declared biological unit."
    else:
        yield "No original input artifact was consumed in the retained attempts."


def _show_results(manifest, answer) -> Iterator[str]:
    from .episode_synthesis import _text

    for key, value in sorted(answer.items()):
        if isinstance(value, list):
            value = f"{len(value)} rows"
        if isinstance(value, dict):
            value = "see the structured answer"
        yield f"- {_text(key)} recorded {_text(value)}."
    for attempt in manifest["execution_dag"]["projection"]["attempt_history"]:
        if attempt["status"] != "succeeded":
            yield f"- Step {_text(attempt['step_id'])} recorded `{attempt['reason']}`."
    for figure in manifest["figures"]:
        yield (
            f"- [{_text(figure['step_id'])} figure]({figure['image']}) with "
            f"[numeric sidecar]({figure['numeric_sidecar']}). Scientific assessment "
            f"was {_text(figure['plot_state']['scientific_assessment'])}."
        )
    if not manifest["figures"]:
        yield "No verified plot or numeric sidecar was retained."
    for missing in manifest["unavailable_figures"]:
        yield (
            f"- Requested figure {missing['figure']} remained unavailable "
            f"with reason `{missing['reason']}`."
        )


def _show_aims(
    aims: list[dict[str, Any]], negative_families: dict[str, list[dict[str, Any]]]
) -> Iterator[str]:
    from .episode_synthesis import _text

    for item in aims:
        yield f"- Aim {_text(item['aim_id'])} recorded `{item['status']}`."
        yield f"  It asks {_text(item['question'])}."
        if item["depends_on"]:
            parents = ", ".join(_text(value) for value in item["depends_on"])
            yield f"  It builds on {parents}."
        outcome = item.get("outcome")
        if outcome and item["status"] == outcome["status"]:
            yield f"  Recorded rationale. {_text(outcome['rationale'])}"
            uncertainty = _text(outcome["uncertainty"])
            yield f"  Remaining uncertainty. {uncertainty}"
        for family in negative_families.get(item["aim_id"], []):
            yield (
                f"  Primary result `{_text(family['step_id'])}` had no significant "
                f"finding among {family['tested_hypotheses']} tested hypotheses "
                f"at q ≤ {item['multiplicity']['fdr']}."
            )
        if panel := item.get("blinded_review"):
            yield (
                f"  Blinded review recorded `{panel['status']}` with "
                f"{panel['submitted_reviews']} of {panel['assigned_reviewers']} "
                "assigned assessments received."
            )
        assessments = {
            row["explanation_id"]: row for row in item["assessed_explanations"]
        }
        stale = item.get("stale_explanation_evidence", {})
        for alternative in item["competing_explanations"]:
            name = _text(alternative["explanation"])
            row = assessments.get(alternative["explanation_id"])
            if reason := stale.get(alternative["explanation_id"]):
                yield f"  The comparison for {name} is stale with reason `{reason}`."
            elif row is not None and item["status"] != "invalidated":
                yield f"  The comparison rated {name} as {_text(row['conclusion'])}."
            else:
                yield f"  The investigation retained {name} as an open explanation."


def _show_blinded_review(metrics: dict[str, Any]) -> Iterator[str]:
    yield (
        f"{metrics['passed']} of {metrics['adjudicated']} adjudicated blinded "
        f"panels passed, from {metrics['panels']} panels in scope. "
        f"{metrics['unresolved_disagreements']} recorded unresolved disagreement "
        f"and {metrics['stale_panels']} had stale evidence."
    )
    if needs_evidence := metrics.get("needs_evidence_panels", 0):
        yield (
            f"Blinded review recorded `needs_evidence` in {needs_evidence} of "
            f"{metrics['panels']} panels."
        )
    for code, count in sorted(metrics["veto_codes"].items()):
        yield f"- Blinded reviewers recorded `{code}` {count} time(s)."


def _show_conclusions(manifest, answer) -> Iterator[str]:
    from .episode_synthesis import _text

    state = manifest["answer"]
    yield f"The investigation recorded status `{manifest['outcome']}`."
    if state["status"] == "withheld":
        yield f"The answer was withheld. {_text(state['reason'])}."
    threshold = manifest["discovery_threshold"]
    if threshold is not None:
        rows = answer.get("gene_statistics", [])
        tested = [
            row.get("q_value", row.get("q"))
            for row in rows
            if row.get("q_value", row.get("q")) is not None
        ]
        if tested and not any(value <= threshold for value in tested):
            yield (
                f"No significant finding among {len(tested)} tested hypotheses "
                f"at q ≤ {threshold}."
            )
    lifecycle = manifest["aim_lifecycle"]
    yield from _show_aims(
        lifecycle["research_aims"], manifest.get("negative_aim_families", {})
    )
    if metrics := lifecycle.get("blinded_review_metrics"):
        yield from _show_blinded_review(metrics)
    for item in manifest["follow_up"]:
        yield (
            f"- Proposed follow-up. {_text(item['experiment'])} "
            f"Uncertainty to resolve. {_text(item['uncertainty'])} "
            f"Discriminating observation. {_text(item['observable'])}"
        )
    yield "Receipt verification establishes execution integrity for the recorded scope."
    yield "Scientific qualification requires independent evidence."
    yield "Clinical release is held."


def render_thesis_summary(manifest: dict[str, Any], answer: dict[str, Any]) -> bytes:
    from .episode_synthesis import _text

    lines = [
        "# RNA-seq thesis research package",
        "",
        "## What data do I have?",
        "",
        *_show_data(manifest),
        "",
        "## What question am I investigating?",
        "",
        _text(manifest["question"]),
        "",
        "## Which methods are appropriate, and why?",
        "",
        "The [versioned methods](methods.md) bind registered procedures to the ledger.",
        "See [provenance](provenance_manifest.json) for requirements and receipts.",
        "",
        "## What happened?",
        "",
        *_show_results(manifest, answer),
        "",
        "## What can I conclude, and what remains uncertain?",
        "",
        *_show_conclusions(manifest, answer),
        *render_literature_comparison(manifest.get("literature_comparison")),
    ]
    return ("\n".join(lines) + "\n").encode()


def _manifest(spec, snapshot, valid, figures, runtime):
    declaration = spec.model_dump(mode="json")
    aims = snapshot["aims"]["research_aims"]
    return {
        "format": FORMAT,
        "question": spec.question,
        "outcome": spec.outcome,
        "primary_branch_id": spec.primary_branch_id,
        "data_inventory": _design_summary(spec, snapshot),
        "aim_lifecycle": snapshot["aims"],
        "requirement_sources": {
            "synthesis": declaration["requirement_sources"],
            "aims": [
                {
                    "aim_id": item["aim_id"],
                    "version": item["version"],
                    "requirements": item["requirement_sources"],
                }
                for item in aims
            ],
        },
        "methods": snapshot["methods"],
        "follow_up": declaration["follow_up"],
        "discovery_threshold": spec.discovery_threshold,
        "runtime": runtime,
        "execution_dag": {
            "lineage_id": snapshot["lineage_id"],
            "revision": snapshot["revision"],
            "adaptive_root_sha256": snapshot["adaptive_root_sha256"],
            "projection": snapshot["dag"],
        },
        "answer_schema_sha256": spec.answer_schema_sha256,
        "answer": valid,
        "fields": {
            name: item.model_dump(mode="json")
            for name, item in snapshot["fields"].items()
        },
        **(
            {"cross_aim_bh": snapshot["joint_publications"]}
            if snapshot["joint_publications"]
            else {}
        ),
        "unavailable_fields": snapshot["missing"],
        "figures": figures,
        "unavailable_figures": snapshot["absent"],
        **(
            {"literature_comparison": snapshot["literature"]}
            if snapshot["literature"] is not None
            else {}
        ),
        "clinical_release_allowed": False,
    }


def _write_package(root: Path, files: dict[str, bytes]) -> str:
    if (
        len(files) > _MAX_FILES
        or sum(map(len, files.values())) > _MAX_PACKAGE_BYTES
        or any(len(raw) > MAX_ARTIFACT_BYTES for raw in files.values())
        or any(
            len(files[name]) > _MAX_METADATA
            for name in (
                "summary.md",
                "methods.md",
                "provenance_manifest.json",
            )
        )
    ):
        raise ValueError("Thesis package exceeds its file or byte budget")
    receipt = canonical_json(
        {
            "format": FORMAT,
            "files": {name: _sha(raw) for name, raw in files.items()},
        }
    )
    root.mkdir(mode=0o700, exist_ok=False)
    try:
        for name, raw in {**files, THESIS_RECEIPT: receipt}.items():
            path = root / name
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with path.open("xb") as target:
                target.write(raw)
    except BaseException:
        shutil.rmtree(root)
        raise
    return _sha(receipt)


def write_thesis_package(
    root: Path,
    workflow: AdaptiveWorkflow,
    spec: ThesisSynthesisSpec,
    *,
    answer_schema: Path,
    open_artifact: OpenArtifact,
    aim_lifecycle: ThesisAimLifecycle | None = None,
    tier_a_executor: TierAExecutor | None = None,
    exploratory_executor: ExploratoryOCI | None = None,
) -> dict[str, Any]:
    """Write the synthesis terminal artifacts from authorized, accepted evidence."""
    from .thesis_replay import replay_files

    root = require_protected_storage(_canonical_path(root))
    schema_raw = read_bounded_file(_canonical_path(answer_schema), MAX_SCHEMA_BYTES)
    if _sha(schema_raw) != spec.answer_schema_sha256:
        raise ValueError("Thesis answer schema digest mismatch")
    schema = decode_receipt_json(schema_raw)
    snapshot = _snapshot(workflow, spec, aim_lifecycle)
    answer, raw, validity = _assemble_answer(spec, schema, snapshot, open_artifact)
    figures, visual_receipts = _figure_files(snapshot, open_artifact)
    replay, runtime = replay_files(
        workflow,
        spec,
        tier_a_executor,
        joint_publications=snapshot["joint_publications"],
        exploratory_executor=exploratory_executor,
    )
    manifest = _manifest(spec, snapshot, validity, visual_receipts, runtime)
    if negative := read_negative_families(
        snapshot["negative_primary_fields"], open_artifact
    ):
        manifest["negative_aim_families"] = negative
    validate_negative_aims(manifest, answer)
    files = {
        "answer_schema.json": schema_raw,
        "provenance_manifest.json": canonical_json(manifest),
        "summary.md": render_thesis_summary(manifest, answer),
        "methods.md": _render_methods(snapshot["methods"], runtime),
        **figures,
        **replay,
    }
    if raw is not None:
        files["final_discovery.json"] = raw
    if workflow.status()["revision"] != spec.expected_revision:
        raise OperationConflict("Thesis evidence changed before publication")
    return {
        "format": FORMAT,
        "root_sha256": _write_package(root, files),
        "answer_status": validity["status"],
        "outcome": spec.outcome,
    }


def _verify_files(root: Path, expected_root: str) -> set[str]:
    raw = read_bounded_file(root / THESIS_RECEIPT, _MAX_METADATA)
    if not _DIGEST.fullmatch(expected_root) or _sha(raw) != expected_root:
        raise ValueError(
            "Thesis package differs from independently retained receipt root"
        )
    receipt = decode_receipt_json(raw)
    files = receipt.get("files") if isinstance(receipt, dict) else None
    if (
        not isinstance(receipt, dict)
        or receipt.get("format") != FORMAT
        or not isinstance(files, dict)
        or len(files) > _MAX_FILES
    ):
        raise ValueError("Malformed thesis package inventory")
    for name, identity in files.items():
        if not _PACKAGED.fullmatch(name) or not isinstance(identity, str):
            raise ValueError("Malformed thesis package artifact identity")
        if hash_bounded_file(root / name, MAX_ARTIFACT_BYTES) != identity:
            raise ValueError(f"Thesis package artifact changed: {name}")
    required = {
        "answer_schema.json",
        "provenance_manifest.json",
        "methods.md",
        "summary.md",
        "replay_bundle/index.json",
        "replay_bundle/replay.py",
        "replay_bundle/parameters.json",
    }
    if not required <= files.keys():
        raise ValueError("Thesis package inventory is incomplete")
    return set(files)


def verify_thesis_package(root: Path, expected_root: str) -> dict[str, Any]:
    """Verify an immutable package against a separately retained root."""
    root = _canonical_path(root)
    paths = _verify_files(root, expected_root)
    manifest = decode_receipt_json(
        read_bounded_file(root / "provenance_manifest.json", _MAX_METADATA)
    )
    schema_raw = read_bounded_file(root / "answer_schema.json", MAX_SCHEMA_BYTES)
    if (
        manifest["format"] != FORMAT
        or manifest["clinical_release_allowed"] is not False
        or _sha(schema_raw) != manifest["answer_schema_sha256"]
    ):
        raise ValueError("Thesis manifest or supplied answer schema changed")
    answer: dict[str, Any] = {}
    if manifest["answer"]["status"] == "validated":
        projected = read_bounded_file(root / "final_discovery.json", MAX_ANSWER_BYTES)
        if (
            "final_discovery.json" not in paths
            or _sha(projected) != manifest["answer"]["sha256"]
        ):
            raise ValueError("Thesis final_discovery.json differs from its binding")
        answer = decode_receipt_json(projected)
        validate_answer(decode_receipt_json(schema_raw), answer)
        for name, value in answer.items():
            verified = manifest["fields"][name]
            validate_gene_fields(
                {name: value}, inferential_scope=verified["claim_scope"] in _INFERENCE
            )
    elif (root / "final_discovery.json").exists():
        raise ValueError("Thesis package contains an unverified final_discovery.json")
    validate_negative_aims(manifest, answer)
    if read_bounded_file(root / "summary.md", _MAX_METADATA) != render_thesis_summary(
        manifest, answer
    ):
        raise ValueError("Thesis summary.md differs from retained evidence")
    if read_bounded_file(root / "methods.md", _MAX_METADATA) != _render_methods(
        manifest["methods"],
        manifest["runtime"],
    ):
        raise ValueError("Thesis methods.md differs from retained evidence")
    return {
        "valid": True,
        "evidence_verified": True,
        "answer_status": manifest["answer"]["status"],
        "root_sha256": expected_root,
        "scope": "package_consistency_with_retained_evidence",
    }
