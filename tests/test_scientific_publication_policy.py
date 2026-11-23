"""Synthetic publication and current-use acceptance checks."""

import json
from hashlib import sha256

import pytest

from devbench.analysis.scientific_publication import (
    CheckedObservation,
    PublicationAccess,
    ReviewEligibility,
    build_publication_descriptor,
    fixed_capsule,
    public_metadata,
    safe_diagnostic,
)
from devbench.core.procedure_profile import ProcedureProfile
from devbench.engine.provenance import canonical_json
from tests.test_erql_publication_families import _allocation, _observed
from tests.test_investigation_invalidation import Worker, link, make, step, wal


def digest(value):
    return sha256(canonical_json(value)).hexdigest()


def profile(*claims):
    return ProcedureProfile(
        profile_id="fixture",
        version=1,
        operations=(
            {
                "operation_id": "fit",
                "output_kinds": ["table"],
                "max_memory_mb": 128,
                "timeout_seconds": 10,
            },
        ),
        input_representations=("counts",),
        biological_units=("donor",),
        permitted_claims=claims,
        qualification_evidence=("a" * 64,)
        if "qualified_procedure_result" in claims
        else (),
    )


def test_fixed_capsule_preserves_family_and_filters_before_rendering():
    allocation = _allocation()
    policy = build_publication_descriptor(
        profile("exploratory_finding"),
        source_sha256="b" * 64,
        allocation=allocation,
        access=PublicationAccess(outcomes=True, statistics=True),
        exploratory=True,
    )
    evidence = (
        _observed(allocation, "first"),
        _observed(allocation, "second", failed=True),
    )
    export = fixed_capsule(policy, allocation, evidence)
    assert [row["status"] for row in export["family_inventory"]] == [
        "ready",
        "withheld",
    ]
    assert export["family_inventory"][1]["counts"]["failed"] == 1
    assert all(row["gene_id"] in {"g1", "g2", "g0"} for row in export["table"])
    assert {row["family_id"] for row in export["table"]} == {"first", "second"}
    assert "q_budget" not in json.dumps(
        export
    ) and "passes_budget_threshold" not in json.dumps(export)
    assert export["figure"]["format"] == "devbench.fixed-effect-figure/1"
    assert "2.0" in export["figure"]["svg"]
    assert export["interpretation"] is None


def test_checked_observation_recomputes_and_is_source_bound():
    source = {"values": [1, 3, 7]}
    policy = build_publication_descriptor(
        profile("checked_observation"),
        source_sha256=digest(source),
        access=PublicationAccess(),
    )
    observation = CheckedObservation.compute("exact_sum", source)
    assert observation.public(policy, source=source)["value"] == 11
    assert "p_value" not in observation.public(policy, source=source)
    stale = build_publication_descriptor(
        profile("checked_observation"),
        source_sha256=digest(source),
        dependencies_current=False,
    )
    with pytest.raises(PermissionError):
        observation.public(stale, source=source)
    with pytest.raises(PermissionError):
        CheckedObservation.compute("exact_sum", {"values": [0]}).public(
            policy, source=source
        )
    with pytest.raises(ValueError):
        CheckedObservation.compute("biological_topology", source)


def test_public_metadata_drops_nested_statistics_and_embargoed_records():
    protected = "b" * 64
    data = {
        "status": "ready",
        "validation": {
            "coverage": 7,
            "nested": {
                "qBudget": 0.001,
                "nominal_significance": True,
                "rank_by": "p_value",
                "error": "private payload 0.017",
            },
        },
        "reviews": [{"artifact_sha256": protected, "observations": {"effect": 919191}}],
        "query_receipt": {"payload": {"effect": 828282}, "q_value_lte": 0.05},
    }
    result = public_metadata(data, blocked_hashes=(protected,))
    encoded = json.dumps(result)
    assert result["validation"]["coverage"] == 7
    assert all(
        value not in encoded
        for value in ("919191", "828282", "0.001", "0.017", "significance", "qBudget")
    )
    assert safe_diagnostic(ValueError("private payload 0.017")) == "invalid_request"


def test_review_and_exposure_are_exact_and_claim_only_holds_keep_table():
    allocation = _allocation()
    kwargs = dict(
        source_sha256="b" * 64,
        allocation=allocation,
        access=PublicationAccess(outcomes=True, statistics=True),
    )
    p = profile(
        "qualified_procedure_result", "checked_observation", "exploratory_finding"
    )
    draft = build_publication_descriptor(p, **kwargs)
    review = ReviewEligibility(
        basis_sha256=draft.basis_sha256,
        design="current",
        numerical="current",
        evidence_first="current",
        narrative="current",
    )
    current = build_publication_descriptor(p, **kwargs, review=review)
    assert current.allow_statistics and current.allow_interpretation
    held = build_publication_descriptor(p, **kwargs, review=review, claim_current=False)
    assert held.allow_statistics and not held.allow_interpretation
    exposed = build_publication_descriptor(
        p,
        **{
            **kwargs,
            "access": PublicationAccess(
                outcomes=True, statistics=True, exposure="target_outcome"
            ),
        },
        review=review,
    )
    assert not exposed.allow_statistics and not exposed.allow_interpretation
    assert exposed.allow_descriptive


def adaptive_subject_fixture(
    root,
    *,
    source_value=None,
    family_id="family",
    exposure_value=None,
    ledger=None,
    lineage_id="study",
):
    """Real accepted adaptive output and same-ledger review/delivery; synthetic only."""
    from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker
    from devbench.analysis.scientific_review_holds import (
        ReviewBinding,
        ReviewEntity,
        ReviewHoldLifecycle,
        obligations_digest,
    )
    from devbench.engine.workflow_operations import WorkflowOperationLedger
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    from tests.test_investigation_invalidation import artifact
    from tests.test_investigation_publication_epochs import authorize
    from tests.test_scientific_result_contract import result_record

    root.mkdir(parents=True, exist_ok=True)
    source = root / "result.json"
    source.write_bytes(
        canonical_json(result_record() if source_value is None else source_value)
    )
    source_sha = sha256(source.read_bytes()).hexdigest()
    node = step("fit")
    exposure = (
        {"classification": "structural"} if exposure_value is None else exposure_value
    )
    ledger = ledger or WorkflowOperationLedger(
        root / "rna-workflow-operations.sqlite", scientific=True
    )
    authority = ledger.scientific
    hashes = {
        "cohort_sha256": "a" * 64,
        "design_sha256": "b" * 64,
        "procedure_sha256": digest(node.profile.model_dump(mode="json")),
        "family_allocation_sha256": digest(None),
        "exposure_basis_sha256": digest(exposure),
        "policy_sha256": "c" * 64,
    }
    authority.commit(
        lineage_id,
        expected_revision=0,
        record={"kind": "binding", "record_id": "binding", **hashes},
    )
    authority.commit(
        lineage_id,
        expected_revision=1,
        record={
            "kind": "grant",
            "record_id": "grant",
            "binding_id": "binding",
            "grant_sha256": "d" * 64,
        },
    )
    authority.activate_execution(
        lineage_id,
        expected_revision=2,
        binding_id="binding",
        grant_id="grant",
        max_jobs=64,
        authorize=authorize,
    )
    flow = AdaptiveWorkflow(ledger, lineage_id)
    flow.insert(node)

    class RetainedWorker(Worker):
        def run(self, envelope):
            receipt = super().run(envelope)
            output = artifact("result-v1").model_copy(update={"sha256": source_sha})
            return receipt.model_copy(update={"outputs": {"out": output}})

    flow.dispatch("fit", RetainedWorker(), actor="synthetic-test")
    lifecycle = ReviewHoldLifecycle(authority, lineage_id)
    lifecycle.register_entities(
        (
            ReviewEntity(entity_id="fit", kind="fit", workflow_step_id="fit"),
            ReviewEntity(
                entity_id="table",
                kind="table",
                dependencies=("fit",),
                family_id=family_id,
            ),
            ReviewEntity(entity_id="claim", kind="claim", dependencies=("table",)),
        )
    )
    lifecycle.register_evidence("result", source_sha, relevant_entity_ids=("table",))
    review_binding = ReviewBinding(
        facts_sha256=hashes["cohort_sha256"],
        inputs_sha256=digest([]),
        exposure_sha256=hashes["exposure_basis_sha256"],
        design_sha256=hashes["design_sha256"],
        procedure_sha256=hashes["procedure_sha256"],
        policy_sha256=hashes["policy_sha256"],
        evidence_sha256=source_sha,
        obligations_sha256=obligations_digest(()),
    )
    broker = EvidenceBroker(authority)
    prefix = "" if lineage_id == "study" else lineage_id + "-"
    policy = AccessPolicy(prefix + "publication", 1, allow_outcomes=True)
    broker.register_policy(policy)
    broker.register_context(
        prefix + "read",
        principal_id="scientist",
        lineage_id=lineage_id,
        role="scientist",
        phase="readback",
        policy_sha256=policy.sha256(),
    )
    broker.register_source(
        prefix + "result",
        content_sha256=source_sha,
        roots=("a" * 64,),
        classification="target_outcome",
    )
    return (
        flow,
        lifecycle,
        broker.delivery(prefix + "read", prefix + "result"),
        source,
        review_binding,
        exposure,
    )


def register_adaptive_table(root):
    from devbench.analysis.scientific_publication import PublicationRegistry

    flow, lifecycle, delivery, source, binding, exposure = adaptive_subject_fixture(
        root
    )
    registry = PublicationRegistry(flow, lifecycle)
    subject = registry.register(
        "table",
        step_id="fit",
        port="out",
        generation=1,
        source_path=source,
        publication_binding=binding,
        design_binding=binding,
        frozen_exposure=exposure,
    )
    return registry, subject, delivery


def newer_publication_obligation(
    registry, subject, *, entity_id, cycle_id="new-obligation-review"
):
    from devbench.analysis.scientific_review_holds import (
        ReviewObligation,
        obligations_digest,
    )

    obligation = ReviewObligation(
        obligation_id=cycle_id,
        entity_id=entity_id,
        stage="post_fit"
        if registry.lifecycle.state()["entities"][entity_id]["kind"] == "table"
        else "claim",
        evidence_ids=("result",),
    )
    binding = subject.publication_binding.model_copy(
        update={"obligations_sha256": obligations_digest((obligation,))}
    )
    registry.lifecycle.register_obligations((obligation,), binding=binding)
    registry.lifecycle.open_cycle(
        cycle_id,
        "publication",
        binding,
        reviewer_id="new-reviewer",
        context_id=cycle_id + "-context",
        narrative_sha256="c" * 64,
        validation_plan_sha256="d" * 64,
        entity_ids=(entity_id,),
    )
    return binding


@wal
@pytest.mark.parametrize(
    "entity_id", ["table", "table-alias", "claim", "unrelated-table"]
)
def test_current_logical_review_obligation_gates_old_subject_exports(
    tmp_path, entity_id
):
    from devbench.analysis.scientific_publication import workspace_guard
    from tests.test_review_hold_lifecycle import complete

    registry, subject, delivery = register_adaptive_table(tmp_path.resolve())
    from devbench.analysis.scientific_review_holds import ReviewEntity

    registry.lifecycle.register_entities(
        (
            ReviewEntity(
                entity_id="table-alias",
                kind="table",
                dependencies=("fit",),
                family_id="family",
            ),
            ReviewEntity(
                entity_id="unrelated-table",
                kind="table",
                dependencies=("fit",),
                family_id="unrelated",
            ),
        )
    )
    complete(registry.lifecycle, current=subject.publication_binding, entities=("fit",))
    complete(
        registry.lifecycle,
        "completed-old-publication",
        current=subject.publication_binding,
        touchpoint="publication",
        entities=("table",),
    )
    assert b"1.23456" in registry.export(
        subject.subject_id, delivery=delivery, format="json"
    )
    guard = workspace_guard(tmp_path.resolve())
    newer_publication_obligation(registry, subject, entity_id=entity_id)
    projected = json.loads(
        registry.export(subject.subject_id, delivery=delivery, format="json")
    )
    numerical_review = registry.descriptor(
        subject.subject_id, delivery=delivery
    ).review.numerical
    assert projected["family_inventory"][0]["measured"] == 2
    assert projected["table"][0]["status"] == "ok"
    if entity_id in {"table", "table-alias"}:
        assert numerical_review == "held"
        assert "effect" not in projected["table"][0]
        assert projected["figure"] is None
        with pytest.raises(PermissionError):
            registry.export(subject.subject_id, delivery=delivery, format="svg")
        with pytest.raises(PermissionError):
            guard.require(subject.source_sha256, subject.subject_id)
    else:
        assert numerical_review == "current"
        assert projected["table"][0]["effect"] == 1.23456
        assert registry.export(
            subject.subject_id, delivery=delivery, format="svg"
        ).startswith(b"<svg")
        guard.require(subject.source_sha256, subject.subject_id)


@wal
@pytest.mark.parametrize("entity_id", ["table", "claim"])
def test_new_review_obligation_is_rechecked_at_attempted_release(
    tmp_path, monkeypatch, entity_id
):
    registry, subject, delivery = register_adaptive_table(tmp_path.resolve())
    original = delivery.broker.mark_attempted

    def race(identity, **kwargs):
        newer_publication_obligation(registry, subject, entity_id=entity_id)
        return original(identity, **kwargs)

    monkeypatch.setattr(delivery.broker, "mark_attempted", race)
    if entity_id == "table":
        with pytest.raises(PermissionError):
            registry.export(subject.subject_id, delivery=delivery, format="csv")
    else:
        assert b"1.23456" in registry.export(
            subject.subject_id, delivery=delivery, format="csv"
        )
    reservations = delivery.broker.exposure(delivery.context_id, delivery.source_id)[
        "reservation_ids"
    ]
    assert len(reservations) == 1
    assert delivery.broker.reservation(reservations[0])["state"] == (
        "reserved" if entity_id == "table" else "delivery_attempted"
    )


@wal
def test_adaptive_publication_releases_only_verified_projection_and_tracks_exact_bytes(
    tmp_path,
):
    registry, subject, delivery = register_adaptive_table(tmp_path.resolve())
    descriptor = registry.descriptor(subject.subject_id, delivery=delivery)
    assert descriptor.allow_descriptive and not descriptor.allow_statistics
    first = registry.export(subject.subject_id, delivery=delivery, format="json")
    second = registry.export(subject.subject_id, delivery=delivery, format="csv")
    assert b"1.23456" in first and b"1.23456" in second
    assert b"p_value" not in first + second and b"standard_error" not in first + second
    assert (
        registry.descriptor(subject.subject_id, delivery=delivery).basis_sha256
        == descriptor.basis_sha256
    )
    reservations = delivery.broker.exposure(delivery.context_id, delivery.source_id)[
        "reservation_ids"
    ]
    assert len(reservations) == 2
    retained = next(
        delivery.broker.reservation(identity)
        for identity in reservations
        if delivery.broker.reservation(identity)["content_sha256"]
        == sha256(second).hexdigest()
    )
    assert retained["content_sha256"] == sha256(second).hexdigest()
    assert retained["publication_projection"]["subject_sha256"] == subject.subject_id
    registry.workflow.hold("fit", reason="changed_source", evidence_sha256="f" * 64)
    with pytest.raises(PermissionError):
        registry.export(subject.subject_id, delivery=delivery, format="json")


@wal
def test_adaptive_publication_review_supersession_cannot_revive_completed_old_binding(
    tmp_path,
):
    from tests.test_review_hold_lifecycle import complete

    registry, subject, delivery = register_adaptive_table(tmp_path.resolve())
    binding = subject.publication_binding
    complete(registry.lifecycle, current=binding, entities=("fit",))
    complete(
        registry.lifecycle,
        "publication-old",
        current=binding,
        touchpoint="publication",
        entities=("table",),
    )
    replacement = registry.register(
        "table",
        step_id="fit",
        port="out",
        generation=1,
        source_path=subject.source_path,
        publication_binding=binding.model_copy(update={"evidence_sha256": "f" * 64}),
        design_binding=binding,
        frozen_exposure={"classification": "structural"},
        prior_subject_id=subject.subject_id,
    )
    with pytest.raises(PermissionError):
        registry.descriptor(subject.subject_id, delivery=delivery)
    assert (
        registry.descriptor(replacement.subject_id, delivery=delivery).review.narrative
        != "current"
    )
    with pytest.raises(
        (PermissionError, ValueError), match="(?:prior|supersed|binding|reviv)"
    ):
        registry.register(
            "table",
            step_id="fit",
            port="out",
            generation=1,
            source_path=subject.source_path,
            publication_binding=binding,
            design_binding=binding,
            frozen_exposure={"classification": "structural"},
            prior_subject_id=replacement.subject_id,
        )


@wal
def test_adaptive_publication_release_rechecks_hold_after_reservation(
    tmp_path, monkeypatch
):
    registry, subject, delivery = register_adaptive_table(tmp_path.resolve())
    original = delivery.broker.mark_attempted

    def race(identity, **kwargs):
        registry.lifecycle.withdraw_use(
            "table", reason="Review withdrew this exact use"
        )
        return original(identity, **kwargs)

    monkeypatch.setattr(delivery.broker, "mark_attempted", race)
    with pytest.raises(PermissionError):
        registry.export(subject.subject_id, delivery=delivery, format="svg")
    retained = delivery.broker.exposure(delivery.context_id, delivery.source_id)
    assert len(retained["reservation_ids"]) == 1
    assert (
        delivery.broker.reservation(retained["reservation_ids"][0])["state"]
        == "reserved"
    )


def register_reviewed_claim(
    registry,
    table,
    *,
    narrative=(
        "The retained coefficient is positive; this is a provisional interpretation."
    ),
    review_exposure=None,
):
    from devbench.analysis.scientific_result_contract import (
        ClaimSpec,
        ClaimValidationPlan,
        ReviewQueryHandle,
    )
    from devbench.analysis.scientific_review import ActiveReviewPacket

    review_exposure = review_exposure or {"classification": "structural"}
    spec = ClaimSpec(
        claim_id="claim",
        revision=1,
        question="Describe the synthetic coefficient",
        population="synthetic",
        biological_unit="donor",
        estimand="contrast",
        scale="log2",
        contrast="B minus A",
        direction="positive",
        claim_class="exploratory_finding",
        effect_fields=("effect",),
        uncertainty_fields=(),
        family_id="family",
        exposure=review_exposure["classification"],
        evidence_ids=("result",),
        required_checks=("inspect",),
    )
    plan = ClaimValidationPlan(
        claim_ids=("claim",),
        competing_explanations=("Fixture only",),
        checks=("inspect",),
        independence_dimension="independent retained evidence review",
        required_evidence=("result",),
        stopping_conditions=("No biological validation",),
        limitations=("Synthetic development fixture",),
    )
    evidence = {"result": table.source_sha256}
    binding = table.publication_binding.model_copy(
        update={
            "evidence_sha256": digest(evidence),
            "exposure_sha256": digest(review_exposure),
            "claim_sha256": digest(
                {
                    "controller_narrative": narrative,
                    "validation_plan": plan.model_dump(mode="json"),
                    "claims": [spec.model_dump(mode="json")],
                }
            ),
        }
    )
    packet = ActiveReviewPacket(
        touchpoint="publication",
        binding=binding,
        question=spec.question,
        evidence=evidence,
        prior_exposure=review_exposure,
        controller_narrative=narrative,
        validation_plan=plan,
        claims=(spec,),
        query_handles=(
            ReviewQueryHandle(
                handle_id="read-result",
                source_id="result",
                context_id="read",
                source_sha256=table.source_sha256,
                bindings_sha256="a" * 64,
                policy_sha256="b" * 64,
                family_ids=("family",),
            ),
        ),
    )
    packet_path = registry.ledger.path.parent / "packet.json"
    packet_path.write_bytes(canonical_json(packet.model_dump(mode="json")))
    claim = registry.register(
        "claim",
        step_id="fit",
        port="out",
        generation=1,
        source_path=table.source_path,
        publication_binding=binding,
        design_binding=table.design_binding,
        frozen_exposure={"classification": "structural"},
        packet_path=packet_path,
    )
    lifecycle = registry.lifecycle
    lifecycle.open_cycle(
        "claim-cycle",
        "publication",
        binding,
        reviewer_id="reviewer",
        context_id="claim-review",
        narrative_sha256=digest(narrative),
        validation_plan_sha256=digest(plan.model_dump(mode="json")),
        entity_ids=("claim",),
        exposure=review_exposure["classification"],
    )
    lifecycle.record_initial_assessment("claim-cycle", assessment_sha256="e" * 64)
    lifecycle.reveal_narrative(
        "claim-cycle",
        narrative_sha256=digest(narrative),
        validation_plan_sha256=digest(plan.model_dump(mode="json")),
    )
    lifecycle.complete_cycle("claim-cycle", assessment_sha256="f" * 64)
    return claim


@wal
def test_adaptive_publication_claim_review_is_typed_and_claim_hold_preserves_table(
    tmp_path,
):
    registry, table, delivery = register_adaptive_table(tmp_path.resolve())
    claim = register_reviewed_claim(registry, table)
    projected = registry.projection(claim.subject_id, delivery=delivery)
    assert projected["interpretation"]["claim_kind"] == "exploratory_finding"
    assert "p_value" not in json.dumps(projected)
    descriptor = registry.descriptor(claim.subject_id, delivery=delivery)
    assert descriptor.allow_interpretation and not descriptor.allow_statistics
    assert "narrative" not in public_metadata(
        {"narrative": "UNBOUND_TEXT"}, descriptor=descriptor
    )
    registry.lifecycle.withdraw_use("claim", reason="Claim withdrawn")
    assert (
        registry.projection(claim.subject_id, delivery=delivery)["interpretation"]
        is None
    )
    assert (
        registry.projection(table.subject_id, delivery=delivery)["table"][0]["effect"]
        == 1.23456
    )
    registry.lifecycle.withdraw_use("table", reason="Whole family withdrawn")
    assert (
        "effect"
        not in registry.projection(table.subject_id, delivery=delivery)["table"][0]
    )


@wal
@pytest.mark.parametrize("format", ["svg", "png", "pdf", "zip"])
def test_adaptive_publication_binary_exports_bound_without_statistics_permission(
    tmp_path, format
):
    import io
    import zipfile

    registry, subject, delivery = register_adaptive_table(tmp_path.resolve())
    raw = registry.export(subject.subject_id, delivery=delivery, format=format)
    assert raw.startswith(
        {"svg": b"<svg", "png": b"\x89PNG", "pdf": b"%PDF", "zip": b"PK"}[format]
    )
    if format == "zip":
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            assert set(archive.namelist()) == {
                "publication.json",
                "table.csv",
                "figure.svg",
                "figure.png",
                "figure.pdf",
            }
            for name in archive.namelist():
                assert b"p_value" not in archive.read(name)
            assert registry._read(subject.source_path) not in [
                archive.read(name) for name in archive.namelist()
            ]
    record = delivery.broker.reservation(
        delivery.broker.exposure("read", "result")["reservation_ids"][-1]
    )
    assert record["content_sha256"] == sha256(raw).hexdigest()
    assert record["publication_projection"]["content_bytes"] == len(raw)


def test_publication_zip_reuses_one_audited_plot_and_matches_standalone_exports(
    tmp_path, monkeypatch
):
    import io
    import zipfile

    from devbench.analysis import bulk_plot_service, scientific_plot_artifacts
    from devbench.analysis.scientific_publication import _render_publication
    from tests.test_bulk_plot_data import result

    source = tmp_path / "result.json"
    source.write_bytes(canonical_json(result(paired=True)))
    workspace = bulk_plot_service._workspace(
        source,
        sha256(source.read_bytes()).hexdigest(),
        run_id="synthetic",
        mode="scientific",
        public=True,
    )
    request = {"panel_id": workspace["panels"][0]["id"], "scope": "complete"}
    projection = {"presentation": {"workspace": workspace, "request": request}}
    counts = {"audit": 0, "coverage": 0}

    def count(key, operation):
        def checked(*args, **kwargs):
            counts[key] += 1
            return operation(*args, **kwargs)

        return checked

    audit = bulk_plot_service.audit_public_scientific_workspace
    coverage = scientific_plot_artifacts.export_coverage
    monkeypatch.setattr(
        bulk_plot_service, "audit_public_scientific_workspace", count("audit", audit)
    )
    monkeypatch.setattr(
        scientific_plot_artifacts, "export_coverage", count("coverage", coverage)
    )
    raw = _render_publication(projection, "zip")
    assert counts == {"audit": 1, "coverage": 1}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for format in ("svg", "png", "pdf"):
            assert archive.read(f"figure.{format}") == _render_publication(
                projection, format
            )


@wal
def test_workspace_owns_adaptive_registration_and_http_release(workspace_tmp_path):
    from devbench.analysis.scientific_publication import PublicationRegistry
    from tests.test_scientific_workspace import action, setup_workspace
    from tests.test_scientific_workspace_http import call, running

    app, _, _, _, _ = setup_workspace(workspace_tmp_path.resolve())
    try:
        action(app, "initialize")
        flow, review, delivery, source, binding, exposure = adaptive_subject_fixture(
            app.workspace / "adaptive"
        )
        registry = PublicationRegistry(flow, review)
        app.scientific.bind_publication_registry(registry)
        app.evidence_delivery = delivery
        subject = app.scientific.register_publication_subject(
            "table",
            step_id="fit",
            port="out",
            generation=1,
            source_path=source,
            publication_binding=binding,
            design_binding=binding,
            frozen_exposure=exposure,
        )
        assert (
            app.scientific.publication_descriptor(subject.source_sha256).scope
            == "adaptive"
        )
        with running(app) as port:
            route = "/api/scientific/publications/" + subject.subject_id
            for format in ("json", "csv", "svg", "png", "pdf", "zip"):
                status, raw, _ = call(port, route + "?format=" + format)
                assert status == 200, raw
                assert b"p_value" not in raw
            review.withdraw_use("table", reason="Current publication withdrawn")
            assert call(port, route + "?format=svg")[0] == 403
    finally:
        app.close()


@wal
def test_workspace_adaptive_projection_fails_closed_and_preserves_mixed_sources(
    workspace_tmp_path,
):
    from devbench.analysis.scientific_publication import PublicationRegistry
    from tests.test_scientific_workspace import action, setup_workspace

    app, _, _, _, _ = setup_workspace(workspace_tmp_path.resolve())
    try:
        action(app, "initialize")
        flow, review, delivery, source, binding, exposure = adaptive_subject_fixture(
            app.workspace / "adaptive"
        )
        registry = PublicationRegistry(flow, review)
        app.scientific.bind_publication_registry(registry)
        app.evidence_delivery = delivery
        source_sha = sha256(source.read_bytes()).hexdigest()
        payload = {
            "results": [
                {"source_sha256": source_sha, "effect": 111.0, "p_value": 0.01},
                {"source_sha256": "f" * 64, "coverage": 7},
            ]
        }
        withheld = app.scientific._project_publication(payload)
        assert withheld["results"][0]["status"] == "withheld"
        assert withheld["results"][1]["coverage"] == 7
        app.scientific.register_publication_subject(
            "table",
            step_id="fit",
            port="out",
            generation=1,
            source_path=source,
            publication_binding=binding,
            design_binding=binding,
            frozen_exposure=exposure,
        )
        projected = app.scientific._project_publication(payload)
        assert projected["results"][0]["publication"]["table"][0]["effect"] == 1.23456
        assert "effect" not in projected["results"][0]
        assert "p_value" not in json.dumps(projected)
        review.withdraw_use("table", reason="Whole table withdrawn")
        held = app.scientific._project_publication(payload)
        assert "effect" not in held["results"][0]["publication"]["table"][0]
        assert held["results"][1]["coverage"] == 7
        assert (
            app.scientific.publication_descriptor(source_sha).allow_descriptive is False
        )
    finally:
        app.close()


@wal
def test_adaptive_registration_rejects_foreign_basis_delivery_and_source(tmp_path):
    from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker

    registry, subject, delivery = register_adaptive_table(tmp_path.resolve())
    foreign, *_ = adaptive_subject_fixture(tmp_path.resolve() / "foreign")
    broker = EvidenceBroker(foreign.authority)
    policy = AccessPolicy("foreign", 1, allow_outcomes=True)
    broker.register_policy(policy)
    broker.register_context(
        "foreign",
        principal_id="scientist",
        lineage_id="study",
        role="scientist",
        phase="readback",
        policy_sha256=policy.sha256(),
    )
    broker.register_source(
        "foreign", content_sha256=subject.source_sha256, roots=("a" * 64,)
    )
    with pytest.raises(PermissionError):
        registry.export(
            subject.subject_id,
            delivery=broker.delivery("foreign", "foreign"),
            format="json",
        )
    with pytest.raises(PermissionError, match="allocation"):
        registry.register(
            "table",
            step_id="fit",
            port="out",
            generation=1,
            source_path=subject.source_path,
            publication_binding=subject.publication_binding,
            design_binding=subject.design_binding,
            frozen_exposure={"classification": "structural"},
            allocation=_allocation(),
            prior_subject_id=subject.subject_id,
        )
    from pathlib import Path

    Path(subject.source_path).write_bytes(b"{}")
    with pytest.raises(PermissionError):
        registry.export(subject.subject_id, delivery=delivery, format="json")


@wal
def test_adaptive_cached_guard_rechecks_whole_family_withdrawal(tmp_path):
    from devbench.analysis.scientific_publication import workspace_guard

    registry, subject, delivery = register_adaptive_table(tmp_path.resolve())
    guard = workspace_guard(tmp_path.resolve())
    guard.require(subject.source_sha256, subject.subject_id)
    registry.lifecycle.withdraw_use("claim", reason="Only claim withdrawn")
    guard.require(subject.source_sha256, subject.subject_id)
    registry.lifecycle.withdraw_use("table", reason="Table withdrawn")
    with pytest.raises(PermissionError):
        guard.require(subject.source_sha256, subject.subject_id)
    assert (
        guard.project({"source_sha256": subject.source_sha256, "effect": 99})["status"]
        == "withheld"
    )


@wal
def test_adaptive_output_without_subject_cannot_fall_back_to_legacy_plot_policy(
    tmp_path,
):
    from types import SimpleNamespace

    from devbench.analysis.bulk_plot_service import _publication_descriptor

    flow, _, delivery, source, _, _ = adaptive_subject_fixture(tmp_path.resolve())
    owner = SimpleNamespace(
        root=tmp_path.resolve(),
        app=SimpleNamespace(workspace=tmp_path.resolve(), evidence_delivery=delivery),
    )
    with pytest.raises(PermissionError):
        _publication_descriptor(owner, "alias", sha256(source.read_bytes()).hexdigest())


@wal
def test_wrong_lineage_registry_cannot_reclassify_adaptive_metadata_or_plot_policy(
    tmp_path,
):
    from types import SimpleNamespace

    from devbench.analysis.bulk_plot_service import _publication_descriptor
    from devbench.analysis.scientific_publication import (
        PublicationRegistry,
        project_subject_metadata,
        publication_descriptor_for_source,
    )
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow

    registry, subject, delivery = register_adaptive_table(tmp_path.resolve())
    wrong = PublicationRegistry(AdaptiveWorkflow(registry.ledger, "other"))
    owner = SimpleNamespace(
        root=tmp_path.resolve(),
        publication_registry=wrong,
        app=SimpleNamespace(workspace=tmp_path.resolve(), evidence_delivery=delivery),
    )
    projected = project_subject_metadata(
        owner,
        {
            "members": [
                {"source_sha256": subject.source_sha256, "effect": 777},
                {"source_sha256": "e" * 64, "coverage": 7},
            ]
        },
    )
    assert projected["members"][0]["status"] == "withheld"
    assert projected["members"][1]["coverage"] == 7
    with pytest.raises(PermissionError):
        publication_descriptor_for_source(owner, subject.source_sha256)
    with pytest.raises(PermissionError):
        _publication_descriptor(owner, "alias", subject.source_sha256)
    with pytest.raises(PermissionError):
        wrong.export(subject.subject_id, delivery=delivery, format="pdf")


@wal
def test_mixed_lineage_metadata_keeps_only_the_attached_lineages_own_projection(
    tmp_path,
):
    from types import SimpleNamespace

    from devbench.analysis.bulk_plot_service import _publication_descriptor
    from devbench.analysis.scientific_publication import (
        PublicationRegistry,
        project_subject_metadata,
    )
    from tests.test_scientific_result_contract import result_record

    first, foreign, first_delivery = register_adaptive_table(tmp_path.resolve())
    saved = result_record()
    saved["rows"][0]["effect"] = 9.0
    flow, review, delivery, source, binding, exposure = adaptive_subject_fixture(
        tmp_path.resolve() / "other",
        source_value=saved,
        ledger=first.ledger,
        lineage_id="other",
    )
    registry = PublicationRegistry(flow, review)
    own = registry.register(
        "table",
        step_id="fit",
        port="out",
        generation=1,
        source_path=source,
        publication_binding=binding,
        design_binding=binding,
        frozen_exposure=exposure,
    )
    owner = SimpleNamespace(
        root=tmp_path.resolve(),
        publication_registry=registry,
        app=SimpleNamespace(workspace=tmp_path.resolve(), evidence_delivery=delivery),
    )
    result = project_subject_metadata(
        owner,
        {
            "members": [
                {"source_sha256": foreign.source_sha256, "effect": 777},
                {"source_sha256": own.source_sha256, "effect": 888},
                {"subject_id": foreign.subject_id, "effect": 999},
                {"source_sha256": "e" * 64, "coverage": 7},
            ]
        },
    )
    assert (
        result["members"][0]
        == result["members"][2]
        == {"status": "withheld", "reason": "publication_source_withheld"}
    )
    assert result["members"][1]["publication"]["table"][0]["effect"] == 9.0
    assert result["members"][3]["coverage"] == 7
    assert (
        _publication_descriptor(owner, "own-alias", own.source_sha256).subject_id
        == own.subject_id
    )
    with pytest.raises(PermissionError):
        registry.descriptor(own.subject_id, delivery=first_delivery)


@wal
@pytest.mark.parametrize("separate_roots", [False, True])
def test_wrong_lineage_workspace_blocks_actual_metadata_plot_pdf_and_archive_routes(
    workspace_tmp_path, separate_roots
):
    import uuid

    from devbench.analysis.bulk_plot_service import _workspace
    from devbench.analysis.scientific_publication import PublicationRegistry
    from devbench.engine.workflow_operations import WorkflowOperationLedger
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    from tests.test_bulk_plot_data import result
    from tests.test_scientific_workspace import (
        action,
        scientific_plan,
        settled,
        setup_workspace,
    )
    from tests.test_scientific_workspace_http import call, running

    saved = result(paired=True)
    app, _, _, method_step, _ = setup_workspace(workspace_tmp_path.resolve())
    try:

        class SavedExecutor:
            def execute(self, contract, inputs, parameters, source, lock, output_dir):
                (output_dir / "result.json").write_bytes(canonical_json(saved))
                return {"exit_code": 0, "runtime": "synthetic_fixture"}

        app.scientific.executor = SavedExecutor()
        _, _, _, plan, _ = scientific_plan(app, method_step)
        settled(
            app,
            action(
                app,
                "run_trial",
                session_id=plan["session_id"],
                experiment_id="qc-pilot",
                allow_data_execution=True,
                idempotency_key=uuid.uuid4().hex,
            ),
        )
        artifact_id = app.scientific.catalog()["result_versions"][0]["artifact_ids"][0]
        flow, review, delivery, source, binding, exposure = adaptive_subject_fixture(
            app.scientific.root if separate_roots else app.workspace / "adaptive",
            source_value=saved,
            family_id="mean_contrast",
        )
        registry = PublicationRegistry(flow, review)
        subject = registry.register(
            "table",
            step_id="fit",
            port="out",
            generation=1,
            source_path=source,
            publication_binding=binding,
            design_binding=binding,
            frozen_exposure=exposure,
        )
        trusted = _workspace(
            source,
            subject.source_sha256,
            run_id=subject.attempt_id,
            mode="scientific",
            public=True,
            descriptor=registry.descriptor(subject.subject_id, delivery=delivery),
        )
        request = {"panel_id": trusted["panels"][0]["id"], "scope": "complete"}
        attached_ledger = (
            WorkflowOperationLedger(
                app.workspace / "rna-workflow-operations.sqlite", scientific=True
            )
            if separate_roots
            else flow.ledger
        )
        app.scientific.bind_publication_registry(
            PublicationRegistry(AdaptiveWorkflow(attached_ledger, "other"))
        )
        app.evidence_delivery = delivery
        metadata = app.scientific._project_publication(
            {
                "members": [
                    {"artifact_id": artifact_id, "effect": 777},
                    {"source_sha256": "e" * 64, "coverage": 7},
                ]
            }
        )
        statuses = {}
        with running(app) as port:
            base = "/api/scientific/artifacts/" + artifact_id
            statuses["plot"] = call(port, base + "/scientific-plots")[0]
            for suffix in ("scientific-pdf", "scientific-bundle"):
                statuses[suffix] = call(
                    port,
                    base + "/" + suffix,
                    body=request,
                    origin="http://127.0.0.1:" + str(port),
                )[0]
        assert {"metadata": metadata["members"][0], "routes": statuses} == {
            "metadata": {"status": "withheld", "reason": "publication_source_withheld"},
            "routes": {"plot": 403, "scientific-pdf": 403, "scientific-bundle": 403},
        }
        assert metadata["members"][1]["coverage"] == 7
    finally:
        app.close()


@wal
@pytest.mark.parametrize("attached_root", ["workspace", "scientific"])
def test_supported_owner_roots_keep_foreign_adaptive_sources_out_of_legacy_projection(
    workspace_tmp_path, attached_root
):
    from devbench.analysis.bulk_plot_service import _publication_descriptor
    from devbench.analysis.scientific_publication import (
        PublicationRegistry,
        publication_descriptor_for_source,
    )
    from tests.test_scientific_result_contract import result_record
    from tests.test_scientific_workspace import action, setup_workspace

    app, _, _, _, _ = setup_workspace(workspace_tmp_path.resolve())
    try:
        action(app, "initialize")
        own_root, foreign_root = (
            (app.workspace, app.scientific.root)
            if attached_root == "workspace"
            else (app.scientific.root, app.workspace)
        )
        foreign_registry, foreign, _ = register_adaptive_table(foreign_root)
        saved = result_record()
        saved["rows"][0]["effect"] = 9.0
        flow, review, delivery, source, binding, exposure = adaptive_subject_fixture(
            own_root, source_value=saved, lineage_id="other"
        )
        registry = PublicationRegistry(flow, review)
        own = registry.register(
            "table",
            step_id="fit",
            port="out",
            generation=1,
            source_path=source,
            publication_binding=binding,
            design_binding=binding,
            frozen_exposure=exposure,
        )
        app.scientific.bind_publication_registry(registry)
        app.evidence_delivery = delivery
        assert foreign_registry.ledger.path != registry.ledger.path
        projected = app.scientific._project_publication(
            {
                "members": [
                    {"source_sha256": foreign.source_sha256, "effect": 777},
                    {"source_sha256": own.source_sha256, "effect": 888},
                    {"subject_id": foreign.subject_id, "effect": 999},
                    {"source_sha256": "e" * 64, "coverage": 7},
                ]
            }
        )
        assert (
            projected["members"][0]
            == projected["members"][2]
            == {"status": "withheld", "reason": "publication_source_withheld"}
        )
        assert projected["members"][1]["publication"]["table"][0]["effect"] == 9.0
        assert projected["members"][3]["coverage"] == 7
        identity = "b" * 32
        app._jobs[identity] = {
            "id": identity,
            "operation_kind": "agentic_episode",
            "status": "completed",
            "downloads": {"result": {"sha256": foreign.source_sha256}},
            "summary": {"effect": 777},
        }
        assert app.status(identity)["publication_status"] == "withheld"
        app._jobs[identity]["downloads"]["result"]["sha256"] = own.source_sha256
        assert app.status(identity)["publication"]["table"][0]["effect"] == 9.0
        for owner in (app.scientific, app):
            with pytest.raises(PermissionError):
                publication_descriptor_for_source(owner, foreign.source_sha256)
        with pytest.raises(PermissionError):
            _publication_descriptor(
                app.scientific, "foreign-alias", foreign.source_sha256
            )
        assert app.scientific.publication_export(own.subject_id, "csv").startswith(
            b"family_id,gene_id,status,effect"
        )
    finally:
        app.close()


@wal
@pytest.mark.parametrize("authority_root", ["workspace", "scientific"])
def test_recorded_nested_authority_remains_withheld_after_actual_restart(
    workspace_tmp_path, authority_root
):
    import uuid

    from devbench.analysis.scientific_publication import PublicationRegistry
    from devbench.engine.workflow_operations import WorkflowOperationLedger
    from devbench.platform.workbench_ui import WorkbenchService
    from tests.test_bulk_plot_data import result
    from tests.test_scientific_workspace import (
        action,
        scientific_plan,
        settled,
        setup_workspace,
    )
    from tests.test_scientific_workspace_http import call, running

    saved = result(paired=True)
    app, methods, _, method_step, inputs = setup_workspace(workspace_tmp_path.resolve())
    try:

        class SavedExecutor:
            def execute(self, contract, inputs, parameters, source, lock, output_dir):
                (output_dir / "result.json").write_bytes(canonical_json(saved))
                return {"exit_code": 0, "runtime": "synthetic_fixture"}

        app.scientific.executor = SavedExecutor()
        _, _, _, plan, _ = scientific_plan(app, method_step)
        settled(
            app,
            action(
                app,
                "run_trial",
                session_id=plan["session_id"],
                experiment_id="qc-pilot",
                allow_data_execution=True,
                idempotency_key=uuid.uuid4().hex,
            ),
        )
        artifact_id = app.scientific.catalog()["result_versions"][0]["artifact_ids"][0]
        nested = (
            (app.workspace if authority_root == "workspace" else app.scientific.root)
            / "adaptive"
            / "retained"
        )
        nested.mkdir(parents=True)
        ledger = WorkflowOperationLedger(
            nested / "custom-authority.sqlite", scientific=True
        )
        flow, review, delivery, source, binding, exposure = adaptive_subject_fixture(
            nested, source_value=saved, family_id="mean_contrast", ledger=ledger
        )
        registry = PublicationRegistry(flow, review)
        app.scientific.bind_publication_registry(registry)
        app.evidence_delivery = delivery
        subject = app.scientific.register_publication_subject(
            "table",
            step_id="fit",
            port="out",
            generation=1,
            source_path=source,
            publication_binding=binding,
            design_binding=binding,
            frozen_exposure=exposure,
        )
        with running(app) as port:
            base = "/api/scientific/artifacts/" + artifact_id
            status, raw, _ = call(port, base + "/scientific-plots")
            assert status == 200, raw
            request = {
                "panel_id": json.loads(raw)["panels"][0]["id"],
                "scope": "complete",
            }
            review.withdraw_use("table", reason="Synthetic current-use withdrawal")
            assert call(port, base + "/scientific-plots")[0] == 403
        workspace, method_root = app.workspace, methods.root
    finally:
        app.close()
    restored = WorkbenchService(
        workspace,
        [inputs],
        enable_scientific=True,
        scientific_workspace=method_root,
        evidence_delivery=delivery,
    )
    try:
        assert restored.scientific.publication_registry is None
        assert not restored.allow_data_execution and not restored.allow_model_execution
        projected = restored.scientific._project_publication(
            {
                "members": [
                    {"artifact_id": artifact_id, "effect": 777},
                    {"source_sha256": "e" * 64, "coverage": 7},
                ]
            }
        )
        statuses = {}
        with running(restored) as port:
            statuses["plot"] = call(port, base + "/scientific-plots")[0]
            for suffix in ("scientific-pdf", "scientific-bundle"):
                statuses[suffix] = call(
                    port,
                    base + "/" + suffix,
                    body=request,
                    origin="http://127.0.0.1:" + str(port),
                )[0]
        assert {"metadata": projected["members"][0], "routes": statuses} == {
            "metadata": {"status": "withheld", "reason": "publication_source_withheld"},
            "routes": {"plot": 403, "scientific-pdf": 403, "scientific-bundle": 403},
        }
        assert projected["members"][1]["coverage"] == 7
        restored.scientific.bind_publication_registry(registry)
        assert not restored.scientific.publication_descriptor(
            subject.source_sha256
        ).allow_descriptive
    finally:
        restored.close()


@wal
def test_recognition_restore_requires_explicit_live_registry_and_delivery(
    workspace_tmp_path,
):
    from devbench.analysis.scientific_publication import _visible_publication_ledgers
    from devbench.platform.workbench_ui import WorkbenchService
    from tests.test_scientific_workspace import action, setup_workspace

    app, methods, _, _, inputs = setup_workspace(workspace_tmp_path.resolve())
    try:
        action(app, "initialize")
        assert _visible_publication_ledgers(app.scientific) == ()
        registry, subject, delivery = register_adaptive_table(
            app.workspace / "nested" / "authority"
        )
        app.scientific.bind_publication_registry(registry)
        before = registry.authority.execution("study")
        workspace, method_root = app.workspace, methods.root
    finally:
        app.close()
    restored = WorkbenchService(
        workspace, [inputs], enable_scientific=True, scientific_workspace=method_root
    )
    try:
        assert registry.ledger.path in _visible_publication_ledgers(restored.scientific)
        assert (
            restored.scientific.publication_registry is None
            and restored.evidence_delivery is None
        )
        assert not restored.allow_data_execution and not restored.allow_model_execution
        assert registry.authority.execution("study") == before
        with pytest.raises(PermissionError):
            restored.scientific.publication_export(subject.subject_id, "json")
        assert (
            restored.scientific._project_publication(
                {"source_sha256": subject.source_sha256, "effect": 777}
            )["status"]
            == "withheld"
        )
        restored.scientific.bind_publication_registry(registry)
        with pytest.raises(PermissionError):
            restored.scientific.publication_export(subject.subject_id, "json")
        restored.evidence_delivery = delivery
        assert b"1.23456" in restored.scientific.publication_export(
            subject.subject_id, "json"
        )
    finally:
        restored.close()


def _damage_recognition_evidence(registry, workspace, record, damage):
    from devbench.analysis.scientific_publication import _LOCATION_LOCK

    if damage == "missing_ledger":
        registry.ledger.path.unlink()
    elif damage == "corrupt_ledger":
        registry.ledger.path.write_bytes(b"not a scientific ledger")
    elif damage == "replaced_ledger":
        copy = registry.ledger.path.with_suffix(".replacement")
        copy.write_bytes(registry.ledger.path.read_bytes())
        copy.replace(registry.ledger.path)
    elif damage == "symlink_ledger":
        alias = workspace / "alias.sqlite"
        alias.symlink_to(registry.ledger.path)
        record["locations"][0]["path"] = "alias.sqlite"
    elif damage == "missing_witness":
        (workspace / _LOCATION_LOCK).unlink()
    elif damage == "symlink_witness":
        witness = workspace / _LOCATION_LOCK
        target = workspace / "other.lock"
        witness.rename(target)
        witness.symlink_to(target)
    elif damage in {"empty_witness", "corrupt_witness"}:
        (workspace / _LOCATION_LOCK).write_bytes(
            b"" if damage == "empty_witness" else b"corrupt witness"
        )


@wal
@pytest.mark.parametrize(
    "damage",
    [
        "missing_record",
        "bad_digest",
        "wrong_project",
        "path_escape",
        "extra_grant",
        "missing_ledger",
        "corrupt_ledger",
        "replaced_ledger",
        "symlink_ledger",
        "missing_witness",
        "symlink_witness",
        "empty_witness",
        "corrupt_witness",
        "writable_marker",
        "too_many_locations",
    ],
)
def test_invalid_recognition_locator_never_becomes_legacy_after_restart(
    workspace_tmp_path, damage
):
    from devbench.analysis.scientific_publication import _LOCATION_KEY
    from devbench.platform.workbench_ui import WorkbenchService
    from tests.test_scientific_workspace import action, setup_workspace

    app, methods, _, _, inputs = setup_workspace(workspace_tmp_path.resolve())
    try:
        action(app, "initialize")
        registry, _, _ = register_adaptive_table(app.workspace / "nested" / "authority")
        app.scientific.bind_publication_registry(registry)
        workspace, method_root = app.workspace, methods.root
    finally:
        app.close()
    marker = workspace / "service.json"
    service = json.loads(marker.read_bytes())
    record = service[_LOCATION_KEY]
    if damage == "missing_record":
        service.pop(_LOCATION_KEY)
    elif damage == "bad_digest":
        record["sha256"] = "f" * 64
    elif damage == "wrong_project":
        record["project_id"] = "f" * 32
    elif damage == "path_escape":
        record["locations"][0]["path"] = "../outside.sqlite"
    elif damage == "extra_grant":
        record["allow_data_execution"] = True
    elif damage == "writable_marker":
        marker.chmod(0o666)
    elif damage == "too_many_locations":
        record["locations"] *= 65
    else:
        _damage_recognition_evidence(registry, workspace, record, damage)
    if damage not in {"missing_record", "bad_digest"}:
        record["sha256"] = digest(
            {key: value for key, value in record.items() if key != "sha256"}
        )
    marker.write_bytes(canonical_json(service))
    with pytest.raises((PermissionError, ValueError)):
        WorkbenchService(
            workspace,
            [inputs],
            enable_scientific=True,
            scientific_workspace=method_root,
        )


@wal
def test_concurrent_publication_binds_preserve_both_durable_locations(
    workspace_tmp_path,
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from devbench.analysis.scientific_publication import (
        PublicationRegistry,
        _visible_publication_ledgers,
        record_publication_authority,
    )
    from devbench.platform.scientific_workspace import ScientificWorkspace
    from devbench.platform.workbench_ui import WorkbenchService
    from tests.test_scientific_workspace import action, setup_workspace

    app, methods, _, _, inputs = setup_workspace(workspace_tmp_path.resolve())
    try:
        action(app, "initialize")
        first, _, _ = register_adaptive_table(app.workspace / "first")
        flow, review, _, source, binding, exposure = adaptive_subject_fixture(
            app.workspace / "second", lineage_id="other"
        )
        second = PublicationRegistry(flow, review)
        second.register(
            "table",
            step_id="fit",
            port="out",
            generation=1,
            source_path=source,
            publication_binding=binding,
            design_binding=binding,
            frozen_exposure=exposure,
        )
        other_owner = ScientificWorkspace(app, app.scientific.root)
        barrier = Barrier(2)

        def bind(owner, registry):
            barrier.wait(timeout=10)
            # Exercise concurrent durable writers independently of the app's
            # in-process UI lock, then attach through the owning entrypoint.
            record_publication_authority(owner, registry)
            owner.bind_publication_registry(registry)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(bind, owner, registry)
                for owner, registry in ((app.scientific, first), (other_owner, second))
            ]
            for future in futures:
                future.result(timeout=20)
        assert {first.ledger.path, second.ledger.path} <= set(
            _visible_publication_ledgers(app.scientific)
        )
        workspace, method_root = app.workspace, methods.root
    finally:
        app.close()
    restored = WorkbenchService(
        workspace, [inputs], enable_scientific=True, scientific_workspace=method_root
    )
    try:
        assert restored.scientific.publication_registry is None
        assert {first.ledger.path, second.ledger.path} <= set(
            _visible_publication_ledgers(restored.scientific)
        )
    finally:
        restored.close()


@wal
def test_noncreator_can_commit_first_locator_without_losing_either_writer(
    workspace_tmp_path, monkeypatch
):
    import os
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from devbench.analysis.scientific_publication import (
        _LOCATION_LOCK,
        PublicationRegistry,
        _visible_publication_ledgers,
        record_publication_authority,
    )
    from tests.test_scientific_workspace import action, setup_workspace

    app, _, _, _, _ = setup_workspace(workspace_tmp_path.resolve())
    try:
        action(app, "initialize")
        first, _, _ = register_adaptive_table(app.workspace / "first")
        flow, review, _, _, _, _ = adaptive_subject_fixture(
            app.workspace / "second", lineage_id="other"
        )
        second = PublicationRegistry(flow, review)
        created, second_done = threading.Event(), threading.Event()
        original_open = os.open
        witness = app.workspace / _LOCATION_LOCK
        completed = []

        def ordered_open(path, flags, *args, **kwargs):
            descriptor = original_open(path, flags, *args, **kwargs)
            if (
                str(path) == str(witness)
                and flags & os.O_CREAT
                and threading.current_thread().name == "locator-creator"
                and not created.is_set()
            ):
                created.set()
                assert second_done.wait(timeout=20), (
                    "Non-creator did not finish while creator paused before flock"
                )
            return descriptor

        def first_writer():
            threading.current_thread().name = "locator-creator"
            record_publication_authority(app.scientific, first)
            completed.append("creator")

        def second_writer():
            try:
                record_publication_authority(app.scientific, second)
                completed.append("noncreator")
            finally:
                second_done.set()

        monkeypatch.setattr(os, "open", ordered_open)
        with ThreadPoolExecutor(max_workers=2) as executor:
            creator = executor.submit(first_writer)
            assert created.wait(timeout=10)
            contender = executor.submit(second_writer)
            contender.result(timeout=25)
            creator.result(timeout=25)
        assert completed == ["noncreator", "creator"]
        assert {first.ledger.path, second.ledger.path} <= set(
            _visible_publication_ledgers(app.scientific)
        )
    finally:
        app.close()


@wal
@pytest.mark.parametrize(
    "kind,value,expected",
    [
        ("exact_sum", {"values": [1, 2, 3]}, 6),
        ("coverage", {"retained": [True, False]}, {"retained": 1, "total": 2}),
        (
            "mathematical_graph_topology",
            {"node_count": 3, "edges": [[0, 1], [1, 2]]},
            {"nodes": 3, "directed_edges": 2, "self_loops": 0, "isolated_nodes": 0},
        ),
    ],
)
def test_checked_adaptive_observations_need_exact_computation_without_review(
    tmp_path, kind, value, expected
):
    from devbench.analysis.scientific_publication import PublicationRegistry

    flow, review, delivery, source, binding, exposure = adaptive_subject_fixture(
        tmp_path.resolve(), source_value=value
    )
    registry = PublicationRegistry(flow, review)
    subject = registry.register(
        "table",
        step_id="fit",
        port="out",
        generation=1,
        source_path=source,
        publication_binding=binding,
        design_binding=binding,
        frozen_exposure=exposure,
        check_id=kind,
    )
    projected = json.loads(
        registry.export(subject.subject_id, delivery=delivery, format="json")
    )
    assert projected["observation"]["value"] == expected
    assert projected["observation"]["claim_kind"] == "checked_observation"
    assert "biological" not in json.dumps(projected) and "p_value" not in json.dumps(
        projected
    )


@wal
def test_adaptive_review_and_access_share_one_snapshot_and_reject_foreign_connection(
    tmp_path,
):
    from devbench.analysis.scientific_publication import access_from_delivery

    registry, subject, delivery = register_adaptive_table(tmp_path.resolve())
    foreign, *_ = adaptive_subject_fixture(tmp_path.resolve() / "foreign")
    with foreign.ledger._database() as db:
        db.execute("BEGIN")
        with pytest.raises(PermissionError):
            registry.lifecycle.review_eligibility(
                subject.publication_binding, "a" * 64, connection=db
            )
        with pytest.raises(PermissionError):
            registry.lifecycle.blocking_entities(
                subject.publication_binding,
                touchpoint="publication",
                entity_ids=("table",),
                connection=db,
            )
        with pytest.raises(PermissionError):
            access_from_delivery(delivery, subject.source_sha256, connection=db)
    with registry.ledger._database() as db:
        db.execute("BEGIN")
        first = registry.descriptor(
            subject.subject_id, delivery=delivery, connection=db
        )
        registry.lifecycle.withdraw_use("table", reason="Concurrent review change")
        assert (
            registry.descriptor(subject.subject_id, delivery=delivery, connection=db)
            == first
        )
    assert not registry.descriptor(
        subject.subject_id, delivery=delivery
    ).allow_descriptive


@wal
def test_adaptive_renderer_rejects_unbound_binary_and_policy_change(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    from devbench.analysis.evidence_access import AccessPolicy

    registry, subject, delivery = register_adaptive_table(tmp_path.resolve())
    rendered = registry.render(subject.subject_id, delivery=delivery, format="svg")
    forged = replace(rendered, content=b"<svg><text>p=0.00001</text></svg>")
    with pytest.raises(PermissionError):
        delivery.broker.reserve("read", "result", forged.content, _publication=forged)
    original = delivery.broker.mark_attempted

    def changed(identity, **kwargs):
        delivery.broker.register_policy(
            AccessPolicy("publication", 2, allow_outcomes=False)
        )
        return original(identity, **kwargs)

    monkeypatch.setattr(delivery.broker, "mark_attempted", changed)
    with pytest.raises(PermissionError):
        registry.export(subject.subject_id, delivery=delivery, format="json")


@wal
def test_adaptive_helper_cannot_accept_caller_review_or_profile_join(tmp_path):
    from devbench.analysis.scientific_publication import adaptive_publication_descriptor
    from devbench.analysis.scientific_result_contract import (
        reviewed_publication_descriptor,
    )

    registry, subject, delivery = register_adaptive_table(tmp_path.resolve())
    with pytest.raises(TypeError):
        adaptive_publication_descriptor(
            registry.workflow,
            "fit",
            "out",
            generation=1,
            expected_sha256=subject.source_sha256,
            delivery=delivery,
            review=ReviewEligibility(),
        )
    with pytest.raises(TypeError):
        reviewed_publication_descriptor(
            registry.lifecycle,
            subject.publication_binding,
            "table",
            subject.profile,
            source_sha256=subject.source_sha256,
            scope="adaptive",
        )
    assert adaptive_publication_descriptor(
        registry.workflow,
        "fit",
        "out",
        generation=1,
        expected_sha256=subject.source_sha256,
        delivery=delivery,
    ) == registry.descriptor(subject.subject_id, delivery=delivery)


@wal
def test_adaptive_alias_http_legacy_binary_and_archive_cannot_bypass_subject_export(
    workspace_tmp_path,
):
    import uuid

    from devbench.analysis.scientific_publication import PublicationRegistry
    from tests.test_bulk_plot_data import result
    from tests.test_scientific_workspace import (
        action,
        scientific_plan,
        settled,
        setup_workspace,
    )
    from tests.test_scientific_workspace_http import call, running

    saved = result(paired=True)
    app, methods, _, method_step, _ = setup_workspace(workspace_tmp_path.resolve())
    try:

        class SavedExecutor:
            def execute(self, contract, inputs, parameters, source, lock, output_dir):
                (output_dir / "result.json").write_bytes(canonical_json(saved))
                return {"exit_code": 0, "runtime": "synthetic_fixture"}

        app.scientific.executor = SavedExecutor()
        _, _, _, plan, _ = scientific_plan(app, method_step)
        settled(
            app,
            action(
                app,
                "run_trial",
                session_id=plan["session_id"],
                experiment_id="qc-pilot",
                allow_data_execution=True,
                idempotency_key=uuid.uuid4().hex,
            ),
        )
        artifact_id = app.scientific.catalog()["result_versions"][0]["artifact_ids"][0]
        flow, review, delivery, source, binding, exposure = adaptive_subject_fixture(
            app.workspace / "adaptive", source_value=saved, family_id="mean_contrast"
        )
        registry = PublicationRegistry(flow, review)
        app.scientific.bind_publication_registry(registry)
        app.evidence_delivery = delivery
        subject = app.scientific.register_publication_subject(
            "table",
            step_id="fit",
            port="out",
            generation=1,
            source_path=source,
            publication_binding=binding,
            design_binding=binding,
            frozen_exposure=exposure,
        )
        assert artifact_id != subject.source_sha256
        with running(app) as port:
            origin = "http://127.0.0.1:" + str(port)
            base = "/api/scientific/artifacts/" + artifact_id
            status, body, _ = call(port, base + "/scientific-plots")
            assert status == 200, body
            workspace = json.loads(body)
            request = {"panel_id": workspace["panels"][0]["id"], "scope": "complete"}
            assert workspace["source"]["publication_subject_id"] == subject.subject_id
            from devbench.analysis.bulk_plot_service import (
                bulk_plot_pdf,
                scientific_presentation_bundle,
            )
            from devbench.analysis.scientific_plot_revisions import (
                load_scientific_revision_bundle,
                replay_scientific_plot_revision,
            )

            with pytest.raises(PermissionError):
                bulk_plot_pdf(workspace, request)
            cached_guard = app.scientific.publication_guard()
            assert bulk_plot_pdf(workspace, request, guard=cached_guard).startswith(
                b"%PDF"
            )
            archived = scientific_presentation_bundle(
                workspace,
                request,
                source_path=methods.artifact_path(artifact_id),
                storage_root=app.workspace / "test-presentations",
                guard=cached_guard,
            )
            for suffix in ("scientific-pdf", "scientific-bundle"):
                status, body, _ = call(
                    port, base + "/" + suffix, body=request, origin=origin
                )
                assert status == 200 and b"p_value" not in body
                assert body.startswith(b"%PDF" if suffix == "scientific-pdf" else b"PK")
            assert call(port, base)[0] == 403
            fresh = "/api/scientific/publications/" + subject.subject_id + "?format=zip"
            assert call(port, fresh)[0] == 200
            review.withdraw_use("table", reason="Whole family review withdrawal")
            with pytest.raises(PermissionError):
                bulk_plot_pdf(workspace, request, guard=cached_guard)
            with pytest.raises(PermissionError):
                scientific_presentation_bundle(
                    workspace,
                    request,
                    source_path=methods.artifact_path(artifact_id),
                    storage_root=app.workspace / "test-presentations",
                    guard=cached_guard,
                )
            assert load_scientific_revision_bundle(
                archived["bytes"],
                archived["root_hash"],
                storage_root=app.workspace / "test-presentations",
            )["verified"]
            assert replay_scientific_plot_revision(
                archived["bytes"],
                archived["root_hash"],
                app.workspace / "historical-replay",
            )["root_hash"]
            assert call(port, base + "/scientific-plots")[0] in {403, 404}
            assert call(port, fresh)[0] == 403
            resolved = app.scientific._project_publication(
                {"artifact_id": artifact_id, "effect": 777}
            )
            assert "effect" not in resolved
    finally:
        app.close()


@wal
def test_episode_http_and_synthesis_use_exact_reviewed_claim_without_raw_report(
    workspace_tmp_path,
):
    from pathlib import Path

    from devbench.analysis.episode_synthesis import public_episode_synthesis
    from tests.test_scientific_workspace import action, setup_workspace
    from tests.test_scientific_workspace_http import call, running

    app, _, _, _, _ = setup_workspace(workspace_tmp_path.resolve())
    try:
        action(app, "initialize")
        registry, table, delivery = register_adaptive_table(app.workspace / "adaptive")
        claim = register_reviewed_claim(registry, table)
        app.scientific.bind_publication_registry(registry)
        app.evidence_delivery = delivery
        rendered = registry.render(claim.subject_id, delivery=delivery, format="json")
        report = {
            "outcome": "completed",
            "targets": [],
            "interpretation": "UNBOUND_REPORT_SENTINEL",
            "p_value": 0.00001,
        }
        view = public_episode_synthesis(report, publication=rendered)
        assert view["interpretation"]["claim_kind"] == "exploratory_finding"
        assert "UNBOUND_REPORT_SENTINEL" not in json.dumps(view)
        identity = "1" * 32
        episode = app.workspace / "jobs" / identity / "episode"
        episode.mkdir(parents=True)
        result_path, synthesis_path = (
            episode / "result.json",
            episode / "synthesis.json",
        )
        result_path.write_bytes(Path(table.source_path).read_bytes())
        synthesis_path.write_bytes(canonical_json(report))
        app._jobs[identity] = {
            "id": identity,
            "operation_kind": "agentic_episode",
            "analyst_profile": "analyst_v1",
            "status": "completed",
            "publication_subject_id": claim.subject_id,
            "summary": {"effect": 999.0, "note": "UNBOUND_REPORT_SENTINEL"},
            "downloads": {
                "result": {"path": str(result_path), "sha256": table.source_sha256},
                "synthesis": {
                    "path": str(synthesis_path),
                    "sha256": sha256(synthesis_path.read_bytes()).hexdigest(),
                },
            },
        }
        from devbench.platform.workbench_ui import _publication_status

        detached = {
            key: value
            for key, value in app._jobs[identity].items()
            if key != "publication_subject_id"
        }
        assert "UNBOUND_REPORT_SENTINEL" not in json.dumps(
            _publication_status(app, detached)
        )
        with running(app) as port:
            for kind in ("summary", "synthesis"):
                status, raw, _ = call(
                    port, "/api/agentic-episodes/" + identity + "/download/" + kind
                )
                assert status == 200, raw
                assert b"provisional interpretation" in raw
                assert b"UNBOUND_REPORT_SENTINEL" not in raw and b"p_value" not in raw
            registry.lifecycle.withdraw_use(
                "claim", reason="Provisional claim withdrawn"
            )
            status, raw, _ = call(
                port, "/api/agentic-episodes/" + identity + "/download/synthesis"
            )
            assert (
                status == 200
                and b"provisional interpretation" not in raw
                and b"1.23456" in raw
            )
            registry.workflow.hold(
                "fit", reason="accepted_output_invalidated", evidence_sha256="f" * 64
            )
            assert (
                call(port, "/api/agentic-episodes/" + identity + "/download/synthesis")[
                    0
                ]
                == 403
            )
    finally:
        app.close()


@wal
def test_adaptive_frozen_exposure_retains_full_binding_and_rejects_foreign_inputs(
    tmp_path,
):
    from devbench.analysis.scientific_publication import PublicationRegistry

    exposure = {
        "classification": "structural",
        "reservation_ids": [],
        "policy_sha256": "a" * 64,
    }
    flow, review, delivery, source, binding, _ = adaptive_subject_fixture(
        tmp_path.resolve(), exposure_value=exposure
    )
    registry = PublicationRegistry(flow, review)
    subject = registry.register(
        "table",
        step_id="fit",
        port="out",
        generation=1,
        source_path=source,
        publication_binding=binding,
        design_binding=binding,
        frozen_exposure=exposure,
    )
    assert subject.decision_exposure == exposure
    with pytest.raises(PermissionError):
        registry.register(
            "table",
            step_id="fit",
            port="out",
            generation=1,
            source_path=source,
            publication_binding=binding.model_copy(update={"inputs_sha256": "f" * 64}),
            design_binding=binding,
            frozen_exposure=exposure,
            prior_subject_id=subject.subject_id,
        )


@wal
@pytest.mark.parametrize(
    "narrative",
    [
        "The probability was 0.001.",
        "The null hypothesis was rejected.",
        "The p-value was small.",
    ],
)
def test_reviewed_exploratory_narrative_cannot_restore_significance_language(
    tmp_path, narrative
):
    registry, table, delivery = register_adaptive_table(tmp_path.resolve())
    claim = register_reviewed_claim(registry, table, narrative=narrative)
    assert (
        registry.projection(claim.subject_id, delivery=delivery)["interpretation"]
        is None
    )


def test_publication_labels_and_codes_do_not_relay_statistical_text():
    from devbench.analysis.scientific_publication import _label

    assert _label("p_value:0.001") is None
    assert (
        safe_diagnostic(PermissionError("publication_subject_required"))
        == "publication_subject_required"
    )
    assert (
        safe_diagnostic(PermissionError("publication_subject_required 0.0001 secret"))
        == "publication_access_denied"
    )


@wal
def test_renamed_investigation_resolves_registered_subject_instead_of_legacy_defaults(
    tmp_path,
):
    from types import SimpleNamespace

    from devbench.analysis.scientific_publication import (
        publication_descriptor_for_source,
    )

    registry, subject, delivery = register_adaptive_table(tmp_path.resolve())
    renamed = tmp_path.resolve() / "renamed"
    renamed.mkdir()
    owner = SimpleNamespace(
        root=renamed, app=SimpleNamespace(workspace=renamed, evidence_delivery=delivery)
    )
    descriptor = publication_descriptor_for_source(owner, subject.source_sha256)
    assert descriptor is not None and descriptor.subject_id == subject.subject_id
    from devbench.analysis.scientific_publication import project_subject_metadata
    from devbench.platform.workbench_ui import _publication_status

    assert (
        project_subject_metadata(
            owner, {"source_sha256": subject.source_sha256, "effect": 777}
        )["status"]
        == "withheld"
    )
    status = _publication_status(
        owner.app,
        {
            "id": "a" * 32,
            "status": "completed",
            "summary": {"effect": 777},
            "downloads": {"result": {"sha256": subject.source_sha256}},
        },
    )
    assert status["publication_status"] == "withheld"
    registry.workflow.revise(
        step("fit").model_copy(update={"parameters_sha256": "b" * 64}),
        reason="method_change",
        evidence_sha256="f" * 64,
    )
    with pytest.raises(PermissionError):
        publication_descriptor_for_source(owner, subject.source_sha256)


@wal
@pytest.mark.parametrize(
    "scale,display",
    [
        ("arbitrary_units", "arbitrary_units"),
        ("log odds", "log odds"),
        ("not declared: ???", "unspecified"),
    ],
)
def test_adaptive_effect_figures_rank_public_effects_on_declared_scale(
    tmp_path, scale, display
):
    from devbench.analysis.scientific_publication import PublicationRegistry
    from tests.test_scientific_result_contract import result_record

    saved = result_record()
    saved["method"]["effect_scale"] = scale
    saved["rows"][1]["effect"] = -2.0
    saved["rows"].reverse()
    flow, review, delivery, source, binding, exposure = adaptive_subject_fixture(
        tmp_path.resolve(), source_value=saved
    )
    registry = PublicationRegistry(flow, review)
    subject = registry.register(
        "table",
        step_id="fit",
        port="out",
        generation=1,
        source_path=source,
        publication_binding=binding,
        design_binding=binding,
        frozen_exposure=exposure,
    )
    projected = json.loads(
        registry.export(subject.subject_id, delivery=delivery, format="json")
    )
    assert [row["gene_id"] for row in projected["table"]] == ["g1", "g2"]
    assert projected["effect_scale"] == display
    assert projected["figure"]["format"] == "devbench.adaptive-effect-figure/1"
    assert (
        display in projected["figure"]["svg"]
        and "log2" not in projected["figure"]["svg"]
    )
    # Although g1 has the smaller q value, the descriptive projection omits q.
    assert projected["figure"]["svg"].index("g2") < projected["figure"]["svg"].index(
        "g1"
    )


def test_fixed_figure_ranks_admitted_q_and_absolute_effects_before_the_mark_limit():
    from xml.etree import ElementTree

    from devbench.analysis.scientific_publication import _effect_figure

    rows = [
        {"gene_id": f"g{index:03}", "effect": 1.0, "q_budget": 0.5}
        for index in range(100)
    ]
    rows.extend(
        [
            {"gene_id": "g100", "effect": 1.0, "q_budget": 0.01},
            {"gene_id": "g101", "effect": -2.0, "q_budget": 0.01},
        ]
    )
    figure = _effect_figure(rows, "b" * 64)
    svg = ElementTree.fromstring(figure["svg"])
    ids = [title.text.split()[0] for title in svg.findall("./{*}circle/{*}title")]
    assert ids[:4] == ["g101", "g100", "g000", "g001"]
    assert len(ids) == 100 and figure["eligible_mark_count"] == 102


def test_fixed_publication_keeps_exact_legacy_figure_verification():
    from devbench.analysis.scientific_plot_revisions import (
        fixed_renderer_identity,
        render_fixed_replay,
    )
    from devbench.analysis.scientific_publication import verify_fixed_publication

    allocation = _allocation()
    policy = build_publication_descriptor(
        profile("exploratory_finding"),
        source_sha256="b" * 64,
        allocation=allocation,
        access=PublicationAccess(outcomes=True),
        exploratory=True,
    )
    evidence = (_observed(allocation, "first"),)
    historical = fixed_capsule(policy, allocation, evidence, _legacy_figure=True)
    figure = historical["figure"]
    assert figure["renderer"] == "fixed_effect_points_v1"
    assert figure["svg"] == (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 43">'
        "<title>Descriptive log2 fold changes in retained feature order</title>"
        '<circle cx="305.0000" cy="15" r="3" fill="#285b75">'
        "<title>g1 2.0</title></circle>"
        '<circle cx="305.0000" cy="24" r="3" fill="#285b75">'
        "<title>g2 2.0</title></circle></svg>"
    )
    assert (
        verify_fixed_publication(historical, historical["publication_sha256"]) == policy
    )
    assert (
        render_fixed_replay(historical, fixed_renderer_identity())["figure.svg"]
        == figure["svg"].encode()
    )
    mismatched = {
        key: value for key, value in historical.items() if key != "publication_sha256"
    }
    mismatched["figure"] = {**figure, "renderer": "fixed_effect_points_v2"}
    with pytest.raises(ValueError, match="figure differs"):
        verify_fixed_publication(
            {**mismatched, "publication_sha256": digest(mismatched)}, digest(mismatched)
        )


@wal
def test_renderer_source_change_blocks_prepared_byte_release(tmp_path, monkeypatch):
    from devbench.analysis import scientific_publication

    registry, subject, delivery = register_adaptive_table(tmp_path.resolve())
    rendered = registry.render(subject.subject_id, delivery=delivery, format="svg")
    assert len(rendered.renderer_sha256) == 64
    monkeypatch.setattr(
        scientific_publication, "_publication_renderer_sha256", lambda: "f" * 64
    )
    with pytest.raises(PermissionError):
        rendered.release()


@wal
def test_review_readback_exposure_does_not_rewrite_the_accepted_decision_basis(
    tmp_path,
):
    from tests.test_review_hold_lifecycle import complete

    registry, table, delivery = register_adaptive_table(tmp_path.resolve())
    complete(registry.lifecycle, current=table.design_binding, entities=("fit",))
    registry.export(table.subject_id, delivery=delivery, format="json")
    actual_exposure = delivery.broker.exposure("read", "result")
    assert actual_exposure["classification"] == "target_outcome"
    claim = register_reviewed_claim(registry, table, review_exposure=actual_exposure)
    descriptor = registry.descriptor(claim.subject_id, delivery=delivery)
    assert descriptor.review.design == "current"
    assert descriptor.allow_interpretation and not descriptor.allow_statistics
    assert (
        claim.publication_binding.exposure_sha256
        != claim.design_binding.exposure_sha256
    )
    assert claim.frozen_exposure.exposure == "structural"
    before = descriptor.basis_sha256
    registry.export(claim.subject_id, delivery=delivery, format="json")
    assert (
        registry.descriptor(claim.subject_id, delivery=delivery).basis_sha256 == before
    )


@wal
def test_later_review_cannot_repair_a_changed_producer_exposure_binding(tmp_path):
    from devbench.engine.scientific_authority import _lookup

    registry, table, delivery = register_adaptive_table(tmp_path.resolve())
    claim = register_reviewed_claim(
        registry, table, review_exposure={"classification": "target_outcome"}
    )
    with registry.ledger._database() as db:
        db.execute("BEGIN")
        original = _lookup(db, "study", table.binding_id)["record"]
        revision = registry.authority._revision(db, "study")
    registry.authority.commit(
        "study",
        expected_revision=revision,
        record={
            **original,
            "record_id": "changed-producer",
            "exposure_basis_sha256": digest({"classification": "unknown"}),
        },
    )
    previous = registry.lifecycle.state()["cycles"]["claim-cycle"]
    registry.lifecycle.open_cycle(
        "late-review",
        "publication",
        claim.publication_binding,
        reviewer_id="reviewer",
        context_id="late-context",
        narrative_sha256=previous["narrative_sha256"],
        validation_plan_sha256=previous["validation_plan_sha256"],
        entity_ids=("claim",),
        exposure="target_outcome",
    )
    registry.lifecycle.record_initial_assessment(
        "late-review", assessment_sha256="e" * 64
    )
    registry.lifecycle.reveal_narrative(
        "late-review",
        narrative_sha256=previous["narrative_sha256"],
        validation_plan_sha256=previous["validation_plan_sha256"],
    )
    registry.lifecycle.complete_cycle("late-review", assessment_sha256="f" * 64)
    with pytest.raises(PermissionError):
        registry.export(claim.subject_id, delivery=delivery, format="json")


@wal
def test_review_exposure_packet_remains_exact_after_decision_exposure_is_separated(
    tmp_path,
):
    from pathlib import Path

    registry, table, delivery = register_adaptive_table(tmp_path.resolve())
    claim = register_reviewed_claim(
        registry, table, review_exposure={"classification": "target_outcome"}
    )
    assert (
        registry.projection(claim.subject_id, delivery=delivery)["interpretation"]
        is not None
    )
    packet = json.loads(Path(claim.packet_path).read_bytes())
    packet["prior_exposure"]["classification"] = "structural"
    Path(claim.packet_path).write_bytes(canonical_json(packet))
    with pytest.raises(PermissionError):
        registry.projection(claim.subject_id, delivery=delivery)


@wal
def test_replay_entitlement_keeps_exact_bytes_outside_public_export(tmp_path):
    from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker
    from devbench.analysis.scientific_publication import (
        access_from_delivery,
        audit_replay_entitlement,
        fixed_csv,
        verify_fixed_publication,
    )
    from devbench.engine.partition_authority import PartitionAuthority

    flow = make(tmp_path.resolve(), name="rna-workflow-operations.sqlite")
    broker = EvidenceBroker(flow.authority)
    access = AccessPolicy("readback", 1, allow_outcomes=True, allow_statistics=True)
    broker.register_policy(access)
    for context, role in (("audit", "auditor"), ("renamed", "scientist")):
        broker.register_context(
            context,
            principal_id="principal",
            lineage_id="study",
            role=role,
            phase="readback",
            policy_sha256=access.sha256(),
        )
    raw = b'{"p_value":0.000031,"private":"RAW_AUDIT_SENTINEL"}'
    raw_sha = sha256(raw).hexdigest()
    broker.register_source(
        "audit-source",
        content_sha256=raw_sha,
        roots=("c" * 64,),
        tags=("audit_replay",),
        classification="target_outcome",
    )
    entitlement = audit_replay_entitlement(
        broker.delivery("audit", "audit-source"), raw_sha
    )
    assert entitlement.release(raw) == raw
    with pytest.raises(PermissionError):
        entitlement.release(b"changed")
    with pytest.raises(PermissionError):
        audit_replay_entitlement(broker.delivery("renamed", "audit-source"), raw_sha)
    assert (
        access_from_delivery(
            broker.delivery("renamed", "audit-source"), raw_sha
        ).exposure
        == "target_outcome"
    )
    broker.register_source(
        "raw-source",
        content_sha256=raw_sha,
        roots=("c" * 64,),
        tags=("original_source_data",),
        classification="target_outcome",
    )
    with pytest.raises(PermissionError):
        audit_replay_entitlement(broker.delivery("audit", "raw-source"), raw_sha)
    allocation = _allocation()
    descriptor = build_publication_descriptor(
        profile("exploratory_finding"),
        source_sha256=raw_sha,
        allocation=allocation,
        access=PublicationAccess(outcomes=True),
        exploratory=True,
    )
    publication = fixed_capsule(
        descriptor, allocation, (_observed(allocation, "first"),)
    )
    assert (
        verify_fixed_publication(publication, publication["publication_sha256"])
        == descriptor
    )
    assert b"p_value" not in fixed_csv(publication)
    assert "RAW_AUDIT_SENTINEL" not in json.dumps(publication)
    publication["figure"]["svg"] += "foreign"
    with pytest.raises(ValueError, match="digest"):
        fixed_csv(publication)

    partition = PartitionAuthority(flow.ledger, "a" * 64)
    partition.protect_outputs((raw_sha,))
    with pytest.raises(PermissionError):
        entitlement.release(raw)


@wal
def test_current_use_and_embargo_survive_content_alias(tmp_path):
    from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker
    from devbench.analysis.scientific_publication import (
        adaptive_publication_descriptor,
        workspace_guard,
    )
    from devbench.engine.partition_authority import PartitionAuthority

    root = tmp_path.resolve()
    flow = make(root, name="rna-workflow-operations.sqlite")
    flow.insert(step("fit"))
    flow.insert(step("plot", link("values", "fit")))
    worker = Worker()
    flow.dispatch("fit", worker, actor="tester")
    flow.dispatch("plot", worker, actor="tester")
    fit = flow.current_use("fit", "out", purpose="export").sha256
    plot = flow.current_use("plot", "out", purpose="export").sha256
    # Accepted computation alone is not a registered publication subject.
    with pytest.raises(PermissionError):
        workspace_guard(root).require(fit, plot)
    broker = EvidenceBroker(flow.authority)
    access = AccessPolicy("publication", 1, allow_outcomes=True)
    broker.register_policy(access)
    broker.register_context(
        "read",
        principal_id="scientist",
        lineage_id="study",
        role="scientist",
        phase="readback",
        policy_sha256=access.sha256(),
    )
    broker.register_source(
        "plot-source",
        content_sha256=plot,
        roots=("c" * 64,),
        classification="target_outcome",
    )

    def descriptor():
        return adaptive_publication_descriptor(
            flow,
            "plot",
            "out",
            generation=1,
            expected_sha256=plot,
            delivery=broker.delivery("read", "plot-source"),
        )

    assert not descriptor().allow_descriptive and not descriptor().allow_statistics
    flow.hold("fit", "withdrawn", evidence_sha256="d" * 64)
    assert not descriptor().allow_descriptive
    renamed = root / "renamed-investigation"
    renamed.mkdir()
    held = "e" * 64
    PartitionAuthority(flow.ledger, "a" * 64).protect_outputs((held,))
    guard = workspace_guard(renamed)
    assert {fit, plot, held} <= guard.blocked_hashes
    from types import SimpleNamespace

    from devbench.platform.workbench_ui import (
        _public_investigation,
        _publication_status,
    )

    app = SimpleNamespace(workspace=renamed)
    assert (
        _publication_status(
            app,
            {
                "intake_binding": {"sources": [{"sha256": held}]},
                "summary": "WITHHELD_SENTINEL",
            },
        )["publication_status"]
        == "withheld"
    )
    assert (
        _public_investigation(
            app, {"source_profiles": [{"sha256": held}], "turns": ["WITHHELD_SENTINEL"]}
        )["publication_status"]
        == "withheld"
    )
    assert (
        guard.project({"result": {"source_sha256": plot, "effect": 333}})["result"][
            "status"
        ]
        == "withheld"
    )
    with pytest.raises(PermissionError):
        guard.require(held)


def test_synthesis_and_audited_observation_withhold_raw_and_unverified_content():
    from devbench.analysis.episode_synthesis import public_episode_synthesis

    report = public_episode_synthesis(
        {
            "outcome": "completed",
            "outputs_complete": True,
            "findings": [{"estimate": {"p": 0.001, "effect": 222}}],
            "narrative": "FORBIDDEN_SENTINEL",
            "targets": [
                {
                    "target_id": "fixed",
                    "estimate_status": "estimated",
                    "hypothesis_family": {
                        "attempted_count": 3,
                        "tested_count": 2,
                        "p": 0.003,
                    },
                }
            ],
        }
    )
    assert report["targets"][0]["hypothesis_family"] == {
        "attempted_count": 3,
        "tested_count": 2,
    }
    assert "FORBIDDEN_SENTINEL" not in json.dumps(report)
    graph = {"node_count": 4, "edges": [[0, 1], [0, 1], [1, 1]]}
    descriptor = build_publication_descriptor(
        profile("checked_observation"),
        source_sha256=digest(graph),
        access=PublicationAccess(),
    )
    proof = CheckedObservation.compute("mathematical_graph_topology", graph)
    assert proof.public(descriptor, source=graph)["value"] == {
        "nodes": 4,
        "directed_edges": 2,
        "self_loops": 1,
        "isolated_nodes": 2,
    }
    with pytest.raises(PermissionError):
        proof.model_copy(update={"value": 444}).public(descriptor, source=graph)


def test_registered_plot_archive_contains_only_admitted_workspace(workspace_tmp_path):
    import io
    import zipfile
    from copy import deepcopy

    from devbench.analysis.bulk_plot_service import (
        _workspace,
        bulk_plot_pdf,
        scientific_presentation_bundle,
    )
    from devbench.analysis.scientific_publication import PublicationGuard
    from tests.test_bulk_plot_data import result

    pytest.importorskip("matplotlib")
    root = workspace_tmp_path.resolve()
    source = root / "source" / "result.json"
    source.parent.mkdir()
    raw = result(paired=True)
    raw["raw_audit"] = "PRIVATE_SOURCE_SENTINEL"
    source.write_text(json.dumps(raw))
    source_sha = sha256(source.read_bytes()).hexdigest()
    for mode in ("auto", "scientific"):
        with pytest.raises(PermissionError):
            _workspace(
                source,
                source_sha,
                run_id="synthetic",
                mode=mode,
                public=True,
                guard=PublicationGuard(frozenset({source_sha})),
            )
    workspace = _workspace(
        source, source_sha, run_id="synthetic", mode="scientific", public=True
    )
    panel = next(
        row for row in workspace["panels"] if row["capability"] == "scatter_marginals"
    )
    request = {"panel_id": panel["id"], "scope": "complete"}
    bad = {
        **request,
        "reference_lines": [{"axis": "x", "value": 1, "label": "p=0.000913"}],
    }
    with pytest.raises(PermissionError):
        bulk_plot_pdf(workspace, bad)
    changed = deepcopy(workspace)
    changed["panels"][0]["title"] = "PRIVATE_SOURCE_SENTINEL"
    with pytest.raises(PermissionError):
        bulk_plot_pdf(changed, request)
    bundle = scientific_presentation_bundle(
        workspace, request, source_path=source, storage_root=root / "presentations"
    )
    with zipfile.ZipFile(io.BytesIO(bundle["bytes"])) as archive:
        names = archive.namelist()
        for name in (
            "payload/workspace.json",
            "payload/manifest.json",
            "payload/figure.svg",
        ):
            captured = archive.read(name)
            assert b"PRIVATE_SOURCE_SENTINEL" not in captured
            assert b'"gene_statistics"' not in captured and b'"q_value"' not in captured
        assert archive.read("payload/figure.pdf").startswith(b"%PDF-")
        assert archive.read("payload/figure.png").startswith(b"\x89PNG")
        assert "payload/source.json" not in names
        assert source.read_bytes() not in [archive.read(name) for name in names]


def test_workspace_projects_nested_results_and_method_preview_before_http(
    workspace_tmp_path, monkeypatch
):
    import http.client
    from types import SimpleNamespace

    from tests.test_scientific_workspace import action, setup_workspace
    from tests.test_scientific_workspace_http import call, running

    app, methods, _, method_step, _ = setup_workspace(workspace_tmp_path.resolve())
    try:
        action(app, "initialize")
        original = app.scientific._catalog

        def catalog():
            value = original()
            value["validations"].append(
                {
                    "status": "passed",
                    "error": "PRIVATE_VALIDATION_SENTINEL",
                    "observations": {
                        "p_value": 0.000091,
                        "raw": "PRIVATE_VALIDATION_SENTINEL",
                    },
                    "evidence": {
                        "root_hash": "a" * 64,
                        "stdout": "PRIVATE_VALIDATION_SENTINEL",
                    },
                }
            )
            value["runs"].append(
                {
                    "status": "succeeded",
                    "execution": {"stdout": "PRIVATE_VALIDATION_SENTINEL"},
                    "trigger": {"kind": "trial", "request": {"p": 0.000091}},
                }
            )
            value["reviews"].append(
                {"decision": "candidate", "rationale": "PRIVATE_VALIDATION_SENTINEL"}
            )
            return value

        monkeypatch.setattr(app.scientific, "_catalog", catalog)
        monkeypatch.setattr(
            app.investigations,
            "events",
            lambda *_args, **_kwargs: {
                "events": [
                    {
                        "type": "tool_finished",
                        "phase": "diagnostics",
                        "status": "succeeded",
                        "sequence": 1,
                        "inputs": {"description": "PRIVATE_VALIDATION_SENTINEL"},
                        "p_value": 0.000091,
                    }
                ],
                "terminal": True,
            },
        )
        assert app.scientific.catalog()["validations"][0]["status"] == "passed"
        assert "PRIVATE_VALIDATION_SENTINEL" not in json.dumps(app.scientific.catalog())
        with running(app) as port:
            status, payload, _ = call(port, "/api/scientific")
            assert (
                status == 200
                and b"PRIVATE_VALIDATION_SENTINEL" not in payload
                and b"0.000091" not in payload
            )
            status, payload, _ = call(
                port, "/api/scientific/methods/" + method_step["method_version_id"]
            )
            assert (
                status == 200
                and b"source_text" not in payload
                and b"source_sha256" in payload
            )
            with monkeypatch.context() as overrides:
                raw = {
                    "status": "passed",
                    "validation_id": "b" * 32,
                    "observations": {
                        "p_value": 0.000091,
                        "note": "PRIVATE_VALIDATION_SENTINEL",
                    },
                }
                overrides.setattr(
                    app.scientific,
                    "store",
                    lambda: SimpleNamespace(resolve=lambda _reference: raw),
                )
                status, payload, _ = call(
                    port,
                    "/api/scientific/resolve?object_type=validation"
                    "&object_id=" + "b" * 32 + "&version_id=" + "a" * 64,
                )
                assert (
                    status == 200
                    and b"PRIVATE_VALIDATION_SENTINEL" not in payload
                    and b"0.000091" not in payload
                )
            with monkeypatch.context() as overrides:

                def error(_body):
                    raise ValueError("PRIVATE_VALIDATION_SENTINEL 0.000091")

                overrides.setattr(app.scientific, "action", error)
                status, payload, _ = call(
                    port, "/api/scientific", body={}, origin=f"http://127.0.0.1:{port}"
                )
                assert (
                    status == 400
                    and json.loads(payload)["message"] == "invalid_request"
                )
                assert b"PRIVATE_VALIDATION_SENTINEL" not in payload
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            try:
                connection.request(
                    "GET", "/api/investigations/" + "a" * 32 + "/events?stream=true"
                )
                stream = connection.getresponse()
                assert stream.status == 200 and stream.readline() == b"event: step\n"
                event = stream.readline()
                assert (
                    b"PRIVATE_VALIDATION_SENTINEL" not in event
                    and b"0.000091" not in event
                )
            finally:
                connection.close()
    finally:
        app.close()


@wal
@pytest.mark.parametrize("row_count", [2, 256])
def test_adaptive_publication_reads_bound_parquet_companion(
    workspace_tmp_path, row_count
):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from devbench.analysis.scientific_publication import PublicationRegistry
    from tests.test_scientific_result_contract import result_record

    document = result_record()
    rows = document.pop("rows")
    if row_count != len(rows):
        rows = [{**rows[-1], "feature_id": f"g{index}"} for index in range(row_count)]
        document["family"].pop("eligible_ids")
    schema = pa.schema(
        [
            ("feature_id", pa.string()),
            ("status", pa.string()),
            ("effect", pa.float64()),
            ("p_value", pa.float64()),
            ("adjusted_p_value", pa.float64()),
            ("standard_error", pa.float64()),
            (
                "missing_reasons",
                pa.struct(
                    [
                        pa.field(name, pa.string())
                        for name in (
                            "effect",
                            "p_value",
                            "adjusted_p_value",
                            "standard_error",
                        )
                    ]
                ),
            ),
        ]
    )
    pending = workspace_tmp_path / "pending.parquet"
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), pending)
    identity = sha256(pending.read_bytes()).hexdigest()
    path = pending.rename(pending.with_name(identity + ".parquet"))
    document.update(
        record_format="devbench.scientific-result-manifest/1",
        rows_ref={
            "evidence_id": "rows",
            "row_count": row_count,
            "sha256": identity,
            "size_bytes": path.stat().st_size,
        },
    )
    flow, lifecycle, delivery, source, binding, exposure = adaptive_subject_fixture(
        workspace_tmp_path,
        source_value=document,
    )
    registry = PublicationRegistry(flow, lifecycle)
    subject = registry.register(
        "table",
        step_id="fit",
        port="out",
        generation=1,
        source_path=source,
        publication_binding=binding,
        design_binding=binding,
        frozen_exposure=exposure,
    )
    result = registry.projection(subject.subject_id, delivery=delivery)
    assert result["family_inventory"][0]["measured"] == row_count
    assert result["table_view"] == {
        "shown_rows": min(128, row_count),
        "total_rows": row_count,
        "status": "complete" if row_count <= 128 else "preview",
    }
    if row_count > 128:
        with pytest.raises(PermissionError, match="publication_format_unavailable"):
            registry.export(subject.subject_id, delivery=delivery, format="csv")
    with path.open("ab") as stream:
        stream.write(b"x")
    with pytest.raises(ValueError, match="byte budget"):
        registry.projection(subject.subject_id, delivery=delivery)
