"""Release obligations for RNA execution and their verified fixture readback.

Answerability describes a question's prerequisites. It does not attest an
executor, approval, execution, output verification, or a Studio result. This
module accepts run observations only from the local release coordinator's
trusted reader. It is not an MCP input schema or a way to import an attestation.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from itertools import product
from typing import Literal

from devbench.core.rna_answerability import (
    RNA_ENTRY_POINTS,
    RNA_FAMILIES,
    RNA_QUESTIONS,
    Assay,
    EntryPoint,
    Measurement,
    QuestionContract,
)
from devbench.engine.workflow_operations import OperationBlocked

_DIGEST = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_SESSION_ID = re.compile(r"[0-9a-f]{32}")
# The fusion prerequisite probe explicitly consumes junction calls alongside
# evidence of its original alignments. The calls themselves are not an entry
# measurement. Other unbound evidence kinds stay visible without a guessed entry.
_SUPPLEMENTAL_ENTRY: dict[str, EntryPoint] = {"fusion_evidence": "alignments"}


@dataclass(frozen=True, slots=True)
class ExecutionRoute:
    family_id: int
    question: str
    assay: Assay
    entry_point: EntryPoint | None
    measurements: tuple[Measurement, ...]
    operation_id: str | None


@dataclass(frozen=True, slots=True)
class ExecutorBinding:
    """A versioned executor and its accepted file formats from trusted code."""

    route: ExecutionRoute
    operation_id: str
    version: str
    runtime_sha256: str
    executor_sha256: str
    input_formats: tuple[str, ...]
    method_contract_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class ReceiptReadback:
    """Identities recovered from a verified current graph or Studio readback."""

    session_id: str
    plan_id: str
    plan_sha256: str
    step_id: str
    attempt_id: str
    accepted_record_id: str
    operation_id: str
    runtime_sha256: str
    executor_sha256: str
    receipt_sha256: str
    artifacts: tuple[tuple[str, str], ...]  # sorted (artifact version, byte digest)


@dataclass(frozen=True, slots=True)
class VerifiedRun:
    """Successful installed-plugin execution observed by a trusted test runner.

    The runner must verify the fixture/input manifest, actual installed MCP
    connection, exact approval, accepted graph receipt, current artifact bytes,
    and an independent response from Studio. Returning this record is a local
    capability of that runner, never a conclusion supplied by the tool caller.
    Dependency and resume identities come from observed execution history.
    """

    binding: ExecutorBinding
    installed_plugin_sha256: str
    fixture_sha256: str
    approved_plan_sha256: str
    approval_receipt_sha256: str
    graph: ReceiptReadback
    studio: ReceiptReadback
    scope: Literal["synthetic", "offline_private"]
    dependency_receipts: tuple[str, ...] = ()
    resumed_from_receipt_sha256: str | None = None


EvidenceReader = Callable[[ExecutorBinding], Iterable[VerifiedRun]]


@dataclass(frozen=True, slots=True)
class CoverageRow:
    route: ExecutionRoute
    binding: ExecutorBinding | None
    blocker: str | None
    receipt_sha256: str | None = None
    required: bool = False


@dataclass(frozen=True, slots=True)
class ExecutionCoverage:
    rows: tuple[CoverageRow, ...]
    offline_private_verified: bool
    multifamily_resume_verified: bool

    @property
    def completed(self) -> int:
        return sum(row.receipt_sha256 is not None for row in self.rows)

    @property
    def uncovered_questions(self) -> tuple[str, ...]:
        completed = {row.route.question for row in self.rows if row.receipt_sha256}
        return tuple(
            question for question in RNA_QUESTIONS if question not in completed
        )

    @property
    def uncovered_promises(self) -> tuple[ExecutionRoute, ...]:
        return tuple(
            dict.fromkeys(
                row.route
                for row in self.rows
                if row.required and not row.receipt_sha256
            )
        )

    @property
    def release_ready(self) -> bool:
        return (
            bool(self.rows)
            and not self.uncovered_questions
            and not self.uncovered_promises
            and self.offline_private_verified
            and self.multifamily_resume_verified
        )


def _variants(question: str, contract: QuestionContract, assay: Assay):
    if (
        question == "differential_expression"
        and assay in RNA_QUESTIONS["differential_cell_state"].assays
    ):
        contract = RNA_QUESTIONS["differential_cell_state"]
    if question == "compositional_abundance":
        yield replace(
            contract,
            measurements=(("latent_embedding",),),
            curated_operation="scrna_neighborhood_da",
            curated_alternatives=(),
        )
        contract = replace(contract, curated_alternatives=())
    if question == "pathway_enrichment":
        yield RNA_QUESTIONS["rna_pathway_camera"]
    yield contract


def execution_inventory() -> tuple[ExecutionRoute, ...]:
    """Expand registered questions into explicit input and candidate runtimes.

    An unrestricted assay declaration is recorded as such across the registry's
    assay inventory. These are candidates from the scientific intake contract.
    Only concrete registered or explicitly promised combinations are required
    individually by the release gate.
    """
    routes = []
    for question, contract in RNA_QUESTIONS.items():
        assays = contract.assays or tuple(RNA_FAMILIES[contract.family_id].priorities)
        for assay in assays:
            for variant in _variants(question, contract, assay):
                operations = (variant.curated_operation, *variant.curated_alternatives)
                for selected in product(*variant.measurements):
                    entry_points = tuple(
                        entry
                        for entry, spec in RNA_ENTRY_POINTS.items()
                        if set(selected).intersection(spec.present_any_of)
                    ) or (_SUPPLEMENTAL_ENTRY.get(question),)
                    measurements = selected
                    if question == "differential_expression" and selected == (
                        "estimated_gene_counts",
                    ):
                        measurements += ("effective_lengths",)
                    routes.extend(
                        ExecutionRoute(
                            contract.family_id,
                            question,
                            assay,
                            entry,
                            measurements,
                            operation,
                        )
                        for entry, operation in product(entry_points, operations)
                    )
    return tuple(dict.fromkeys(routes))


def _binding_is_valid(
    binding: ExecutorBinding,
    routes: set[ExecutionRoute],
    scientific_scopes: set[ExecutionRoute],
) -> bool:
    route = binding.route
    contract = binding.method_contract_sha256
    curated = route in routes and binding.operation_id == route.operation_id
    # Register custom runtime coverage as a distinct row. It cannot certify a
    # promised curated implementation. Context-anchored entrypoints need the
    # registered method's additional input to be checked by the fixture reader.
    custom = (
        route.operation_id is None
        and contract is not None
        and (
            route in scientific_scopes
            or replace(route, entry_point=None) in scientific_scopes
        )
    )
    return (
        (curated or custom)
        and (contract is None or bool(_DIGEST.fullmatch(contract)))
        and route.entry_point in RNA_ENTRY_POINTS
        and bool(_ID.fullmatch(binding.operation_id) and _ID.fullmatch(binding.version))
        and bool(_DIGEST.fullmatch(binding.runtime_sha256))
        and bool(_DIGEST.fullmatch(binding.executor_sha256))
        and bool(binding.input_formats)
        and all(_ID.fullmatch(item) for item in binding.input_formats)
    )


def _matches_run(binding: ExecutorBinding, run: VerifiedRun) -> bool:
    graph = run.graph
    return (
        run.binding == binding
        and graph == run.studio
        and run.scope in {"synthetic", "offline_private"}
        and graph.plan_sha256 == run.approved_plan_sha256
        and graph.operation_id == binding.operation_id
        and graph.runtime_sha256 == binding.runtime_sha256
        and graph.executor_sha256 == binding.executor_sha256
        and bool(
            _SESSION_ID.fullmatch(graph.session_id)
            and _SESSION_ID.fullmatch(graph.plan_id)
        )
        and all(
            _ID.fullmatch(value)
            for value in (
                graph.step_id,
                graph.attempt_id,
                graph.accepted_record_id,
            )
        )
        and all(
            _DIGEST.fullmatch(value)
            for value in (
                run.installed_plugin_sha256,
                run.fixture_sha256,
                run.approval_receipt_sha256,
                graph.plan_sha256,
                graph.receipt_sha256,
                *run.dependency_receipts,
            )
        )
        and bool(graph.artifacts)
        and len(graph.artifacts) == len({identity for identity, _ in graph.artifacts})
        and all(
            _ID.fullmatch(identity) and _DIGEST.fullmatch(value)
            for identity, value in graph.artifacts
        )
        and (
            run.resumed_from_receipt_sha256 is None
            or run.resumed_from_receipt_sha256 in run.dependency_receipts
        )
    )


def _verified_reads(binding: ExecutorBinding, observe: EvidenceReader | None):
    if observe is None:
        return (), "installed_fixture_run_missing"
    try:
        observations = tuple(observe(binding))
        verified = {}
        for run in observations:
            if type(run) is not VerifiedRun or not _matches_run(binding, run):
                continue
            identity = run.graph.receipt_sha256
            if identity in verified and verified[identity] != run:
                return (), "execution_evidence_mismatch"
            verified[identity] = run
    except OperationBlocked as error:
        reason = (
            error.state
            if _ID.fullmatch(error.state)
            else "execution_evidence_unavailable"
        )
        return (), reason
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return (), "execution_evidence_unavailable"
    if verified:
        return tuple(verified.values()), None
    return (
        (),
        "execution_evidence_mismatch"
        if observations
        else "installed_fixture_run_missing",
    )


def _resume_verified(runs: dict[str, VerifiedRun]) -> bool:
    for consumer in runs.values():
        parent = runs.get(consumer.resumed_from_receipt_sha256 or "")
        if (
            parent is not None
            and parent.graph.session_id == consumer.graph.session_id
            and parent.graph.step_id != consumer.graph.step_id
            and parent.binding.route.family_id != consumer.binding.route.family_id
            and parent.installed_plugin_sha256 == consumer.installed_plugin_sha256
            and parent.fixture_sha256 == consumer.fixture_sha256
            and parent.scope == consumer.scope
        ):
            return True
    return False


def evaluate_execution_coverage(
    bindings: Iterable[ExecutorBinding] = (),
    *,
    observe: EvidenceReader | None = None,
    promised_routes: Iterable[ExecutionRoute] = (),
) -> ExecutionCoverage:
    """Count only observed, scope-matched verified completions for release.

    Pass requires coverage for every registered question as well as each
    concrete binding and explicit promise from the trusted release manifest.
    The default has no installed-plugin observations and cannot pass.
    """
    inventory = execution_inventory()
    known = set(inventory)
    scientific_scopes = {replace(route, operation_id=None) for route in inventory}
    applied = defaultdict(set)
    for binding in bindings:
        if type(binding) is not ExecutorBinding or not _binding_is_valid(
            binding, known, scientific_scopes
        ):
            raise ValueError("Invalid registered RNA executor binding")
        applied[binding.route].add(binding)
    promised = set(promised_routes)
    if not promised <= known.union(applied):
        raise ValueError("RNA execution promise is outside the question registry")
    custom_routes = sorted(set(applied) - known, key=repr)
    rows, runs = [], {}
    for route in (*inventory, *custom_routes):
        if route not in applied:
            if route.entry_point is None:
                reason = "entry_format_unregistered"
            elif route.operation_id is None:
                reason = "runtime_unavailable"
            else:
                reason = "graph_executor_unbound"
            rows.append(CoverageRow(route, None, reason, required=route in promised))
            continue
        for binding in sorted(
            applied[route],
            key=lambda value: (
                value.operation_id,
                value.version,
                value.runtime_sha256,
                value.executor_sha256,
                value.input_formats,
                value.method_contract_sha256 or "",
            ),
        ):
            verified, reason = _verified_reads(binding, observe)
            conflict = any(
                run.graph.receipt_sha256 in runs
                and runs[run.graph.receipt_sha256] != run
                for run in verified
            )
            if conflict:
                rows.append(
                    CoverageRow(
                        route,
                        binding,
                        "execution_evidence_mismatch",
                        required=True,
                    )
                )
                continue
            runs.update((run.graph.receipt_sha256, run) for run in verified)
            receipt = (
                None
                if not verified
                else min(run.graph.receipt_sha256 for run in verified)
            )
            rows.append(CoverageRow(route, binding, reason, receipt, required=True))
    return ExecutionCoverage(
        tuple(rows),
        any(run.scope == "offline_private" for run in runs.values()),
        _resume_verified(runs),
    )


def require_execution_coverage(
    bindings: Iterable[ExecutorBinding] = (),
    *,
    observe: EvidenceReader | None = None,
    promised_routes: Iterable[ExecutionRoute] = (),
) -> ExecutionCoverage:
    report = evaluate_execution_coverage(
        bindings,
        observe=observe,
        promised_routes=promised_routes,
    )
    if not report.release_ready:
        raise OperationBlocked("rna_execution_coverage_incomplete")
    return report
