"""Exercise the release gate independently of prerequisite-only probes."""

from dataclasses import replace

import pytest


def test_inventory_keeps_all_contracts_and_distinguishes_input_representations():
    from devbench.core.rna_answerability import RNA_QUESTIONS
    from devbench.platform.rna_execution_coverage import execution_inventory

    routes = execution_inventory()
    assert {route.family_id for route in routes} == set(range(1, 21))
    assert {route.question for route in routes} == set(RNA_QUESTIONS)
    assert len(routes) == len(set(routes))
    assert any(
        route.question == "fusion_evidence" and route.entry_point == "alignments"
        for route in routes
    )
    assert any(
        route.question == "rna_modification" and route.entry_point is None
        for route in routes
    )
    bulk = [
        route
        for route in routes
        if route.question == "differential_expression" and route.assay == "bulk_rna_seq"
    ]
    assert any(
        route.entry_point == "count_matrix" and route.measurements == ("gene_counts",)
        for route in bulk
    )
    assert any(
        route.entry_point == "transcript_quantification"
        and route.measurements == ("estimated_gene_counts", "effective_lengths")
        for route in bulk
    )
    assert any(
        route.entry_point == "normalized_expression"
        and route.measurements == ("normalized_expression",)
        for route in bulk
    )
    assert any(
        route.question == "compositional_abundance"
        and route.operation_id == "scrna_neighborhood_da"
        and route.measurements == ("latent_embedding",)
        for route in routes
    )


def test_catalogue_availability_is_not_completed_execution():
    from devbench.core.rna_answerability import RNA_QUESTIONS
    from devbench.engine.workflow_operations import OperationBlocked
    from devbench.platform.rna_execution_coverage import (
        evaluate_execution_coverage,
        require_execution_coverage,
    )

    report = evaluate_execution_coverage()
    assert report.completed == 0
    assert not report.release_ready
    assert not report.offline_private_verified
    assert not report.multifamily_resume_verified
    assert set(report.uncovered_questions) == set(RNA_QUESTIONS)
    blockers = {row.blocker for row in report.rows}
    assert {
        "entry_format_unregistered",
        "runtime_unavailable",
        "graph_executor_unbound",
    } <= blockers
    with pytest.raises(OperationBlocked, match="rna_execution_coverage_incomplete"):
        require_execution_coverage()


def _binding(question, assay, entry, operation):
    from devbench.platform.rna_execution_coverage import (
        ExecutorBinding,
        execution_inventory,
    )

    route = next(
        route
        for route in execution_inventory()
        if (route.question, route.assay, route.entry_point, route.operation_id)
        == (question, assay, entry, operation)
    )
    return ExecutorBinding(route, operation, "1", "1" * 64, "a" * 64, ("csv", "csv"))


def _proof(binding, *, step, receipt):
    from devbench.platform.rna_execution_coverage import (
        ReceiptReadback,
        VerifiedRun,
    )

    readback = ReceiptReadback(
        session_id="a" * 32,
        plan_id="b" * 32,
        plan_sha256="c" * 64,
        step_id=step,
        attempt_id=receipt[:32],
        accepted_record_id="accepted-" + step,
        operation_id=binding.operation_id,
        runtime_sha256=binding.runtime_sha256,
        executor_sha256=binding.executor_sha256,
        receipt_sha256=receipt,
        artifacts=(("result", "d" * 64),),
    )
    return VerifiedRun(
        binding=binding,
        installed_plugin_sha256="e" * 64,
        fixture_sha256="f" * 64,
        approved_plan_sha256=readback.plan_sha256,
        approval_receipt_sha256="2" * 64,
        graph=readback,
        studio=readback,
        scope="offline_private",
    )


@pytest.mark.parametrize(
    "change",
    ["approval", "plan", "runtime", "artifact", "code", "unaccepted"],
)
def test_inconsistent_observation_does_not_count_as_completed(change):
    from devbench.platform.rna_execution_coverage import evaluate_execution_coverage

    binding = _binding(
        "differential_expression",
        "bulk_rna_seq",
        "count_matrix",
        "bulk_condition_contrast",
    )
    proof = _proof(binding, step="model", receipt="3" * 64)
    if change == "approval":
        proof = replace(proof, approved_plan_sha256="4" * 64)
    if change == "plan":
        proof = replace(proof, studio=replace(proof.studio, plan_sha256="4" * 64))
    if change == "runtime":
        proof = replace(proof, graph=replace(proof.graph, runtime_sha256="4" * 64))
    if change == "artifact":
        proof = replace(proof, studio=replace(proof.studio, artifacts=()))
    if change == "code":
        old = replace(proof.graph, executor_sha256="4" * 64)
        proof = replace(proof, graph=old, studio=old)
    if change == "unaccepted":
        unaccepted = replace(proof.graph, accepted_record_id="")
        proof = replace(proof, graph=unaccepted, studio=unaccepted)

    report = evaluate_execution_coverage((binding,), observe=lambda _: (proof,))
    row = next(row for row in report.rows if row.binding == binding)
    assert row.blocker == "execution_evidence_mismatch"
    assert report.completed == 0
    assert not report.offline_private_verified


def test_counting_verified_runs_requires_actual_resume_dependency():
    from devbench.platform.rna_execution_coverage import evaluate_execution_coverage

    expression = _binding(
        "differential_expression",
        "bulk_rna_seq",
        "count_matrix",
        "bulk_condition_contrast",
    )
    pathway = _binding(
        "pathway_enrichment", "bulk_rna_seq", "prior_results", "rna_pathway_tested"
    )
    model = _proof(expression, step="model", receipt="3" * 64)
    enrichment = _proof(pathway, step="enrichment", receipt="4" * 64)
    proofs = {expression: (model,), pathway: (enrichment,)}
    report = evaluate_execution_coverage(proofs, observe=proofs.__getitem__)
    assert report.completed == 2
    assert report.offline_private_verified
    assert not report.multifamily_resume_verified
    assert not report.release_ready
    assert "differential_expression" not in report.uncovered_questions

    proofs[pathway] = (
        replace(
            enrichment,
            dependency_receipts=(model.graph.receipt_sha256,),
            resumed_from_receipt_sha256=model.graph.receipt_sha256,
        ),
    )
    report = evaluate_execution_coverage(proofs, observe=proofs.__getitem__)
    assert report.multifamily_resume_verified


def test_unbound_promised_combination_is_required():
    from devbench.platform.rna_execution_coverage import evaluate_execution_coverage

    binding = _binding(
        "differential_expression",
        "bulk_rna_seq",
        "count_matrix",
        "bulk_condition_contrast",
    )
    count_route = binding.route
    report = evaluate_execution_coverage(promised_routes=(count_route,))
    assert report.uncovered_promises == (count_route,)
    assert next(row for row in report.rows if row.route == count_route).required
    assert not report.release_ready


def test_custom_executor_cannot_take_credit_for_a_promised_curated_operation():
    from devbench.platform.rna_execution_coverage import evaluate_execution_coverage

    curated = _binding(
        "differential_expression",
        "bulk_rna_seq",
        "count_matrix",
        "bulk_condition_contrast",
    )
    substitute = replace(
        curated, operation_id="custom_expression", method_contract_sha256="8" * 64
    )
    with pytest.raises(ValueError, match="Invalid registered RNA executor binding"):
        evaluate_execution_coverage((substitute,))

    custom = replace(substitute, route=replace(curated.route, operation_id=None))
    report = evaluate_execution_coverage(
        (custom,),
        observe=lambda _: (_proof(custom, step="custom-step", receipt="7" * 64),),
        promised_routes=(curated.route,),
    )
    assert report.completed == 1
    assert report.uncovered_promises == (curated.route,)


def test_caller_assertions_and_prerequisite_attestations_do_not_count():
    from devbench.core.rna_answerability import RuntimeEvidence
    from devbench.platform.rna_execution_coverage import evaluate_execution_coverage

    binding = _binding(
        "differential_expression",
        "bulk_rna_seq",
        "count_matrix",
        "bulk_condition_contrast",
    )
    attestation = RuntimeEvidence(
        question=binding.route.question,
        tier="tier_a",
        operation_id=binding.operation_id,
        runtime_sha256=binding.runtime_sha256,
    )
    report = evaluate_execution_coverage(
        (binding,),
        observe=lambda _: (attestation, {"status": "completed", "verified": True}),
    )
    assert report.completed == 0
    assert not report.release_ready


def test_release_checks_each_registered_runtime_even_after_all_questions_are_seen():
    from devbench.platform.rna_execution_coverage import (
        ExecutorBinding,
        evaluate_execution_coverage,
        execution_inventory,
        require_execution_coverage,
    )

    targets = {}
    for route in execution_inventory():
        targets.setdefault(route.question, route)
    observed = {}
    parent = None
    # Unit-test witnesses for the gate itself. These are not installed fixture
    # results or evidence about any scientific execution route.
    for index, route in enumerate(targets.values(), 1):
        custom = route.operation_id is None
        if route.entry_point is None:
            route = replace(route, entry_point="annotated_object")
        binding = ExecutorBinding(
            route,
            route.operation_id or "custom_executor",
            "1",
            "1" * 64,
            "a" * 64,
            ("registered-format",),
            method_contract_sha256="2" * 64 if custom else None,
        )
        proof = _proof(binding, step=f"step-{index}", receipt=f"{index:064x}")
        if parent is None:
            parent = proof
        elif route.family_id != parent.binding.route.family_id:
            digest = parent.graph.receipt_sha256
            proof = replace(
                proof, dependency_receipts=(digest,), resumed_from_receipt_sha256=digest
            )
        observed[binding] = (proof,)
    complete = require_execution_coverage(observed, observe=observed.__getitem__)
    assert complete.completed == len(targets)
    assert complete.release_ready

    additional = replace(next(iter(observed)), version="2", executor_sha256="b" * 64)
    report = evaluate_execution_coverage(
        (*observed, additional), observe=lambda binding: observed.get(binding, ())
    )
    assert not report.uncovered_questions
    assert report.uncovered_promises == (additional.route,)
    assert not report.release_ready


def test_missing_and_blocked_runs_retain_specific_blocker():
    from devbench.engine.workflow_operations import OperationBlocked
    from devbench.platform.rna_execution_coverage import evaluate_execution_coverage

    binding = _binding(
        "differential_expression",
        "bulk_rna_seq",
        "count_matrix",
        "bulk_condition_contrast",
    )

    def observe(_):
        raise OperationBlocked("approval_required")

    report = evaluate_execution_coverage((binding,), observe=observe)
    assert (
        next(row for row in report.rows if row.binding == binding).blocker
        == "approval_required"
    )
    report = evaluate_execution_coverage((binding,))
    assert (
        next(row for row in report.rows if row.binding == binding).blocker
        == "installed_fixture_run_missing"
    )
