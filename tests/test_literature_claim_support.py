"""Offline Literature custody and actual challenger-entrypoint controls."""

import hashlib
import json
from pathlib import Path

import pytest

from devbench.analysis.checkpoints import digest
from tests.test_evidence_access_policy import setup

PUBMED = (
    b"<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID><Article>\n"
    b"<ArticleTitle>Human airway response</ArticleTitle><Journal><JournalIssue>"
    b"<PubDate><Year>2024</Year></PubDate></JournalIssue></Journal>\n"
    b'<Abstract><AbstractText Label="RESULTS">Dexamethasone reduced expression in '
    b"human airway cells.</AbstractText></Abstract>\n"
    b"</Article></MedlineCitation><PubmedData><ArticleIdList>"
    b'<ArticleId IdType="pubmed">123</ArticleId>'
    b'<ArticleId IdType="doi">10.1000/test</ArticleId></ArticleIdList>'
    b"</PubmedData></PubmedArticle></PubmedArticleSet>"
)


def api():
    from devbench.analysis import literature_adapter

    return literature_adapter


def claim():
    from devbench.analysis.scientific_result_contract import ClaimSpec

    return ClaimSpec(
        claim_id="claim",
        revision=1,
        question="What is associated with treatment?",
        population="airway cells",
        biological_unit="donor",
        estimand="relative expression",
        scale="log2",
        contrast="treated versus control",
        direction="negative",
        claim_class="biological_interpretation",
        effect_fields=("effect",),
        uncertainty_fields=(),
        family_id=None,
        exposure="unknown",
        evidence_ids=("numeric",),
        required_checks=("literature",),
    )


def service(tmp_path, monkeypatch, responses=None, **limits):
    m = api()
    from devbench.analysis.scientific_review_holds import (
        ReviewEntity,
        ReviewHoldLifecycle,
    )

    ledger, broker, _ = setup(
        tmp_path / "authority", role="challenger", phase="publication_initial"
    )
    lifecycle = ReviewHoldLifecycle(ledger.scientific, "study")
    lifecycle.register_entities((ReviewEntity(entity_id="claim", kind="claim"),))
    capability = m.LiteratureCapability(plugin_root=str(tmp_path / "plugin"))
    monkeypatch.setattr(
        m.LiteratureCapability,
        "verify",
        lambda self: {"version": "0.1.5", "client_sha256": m.PINNED_CLIENTS},
    )
    calls = []

    def fake_hop(host, target, deadline, maximum, **kwargs):
        calls.append((host, target))
        body = (responses or {}).get(target, PUBMED)
        if isinstance(body, Exception):
            raise body
        if len(body) > maximum:
            raise ValueError("Source response exceeds byte limit")
        return (
            None,
            body,
            "application/json" if body.startswith(b"{") else "application/xml",
        )

    from devbench.analysis import method_sources

    monkeypatch.setattr(method_sources, "_fetch_hop", fake_hop)
    instance = m.LiteratureService(
        ledger.scientific,
        "study",
        tmp_path / "literature",
        broker=broker,
        capability=capability,
        limits=m.LiteratureLimits(**limits),
        allow_network=True,
        data_scope="synthetic",
    )
    binding = m.LiteratureClaimBinding(
        claim_id="claim",
        claim_sha256=digest(claim().model_dump(mode="json")),
        evidence_sha256=digest("numerics"),
        review_binding_sha256=digest("review"),
        obligation_id="literature",
        context=m.BiologicalContext(
            organism="human",
            tissue="airway",
            intervention="dexamethasone",
            direction="decreased",
            uncertainty="observational",
        ),
    )
    instance.bind_claim(binding)
    return instance, binding, calls, lifecycle


def test_raw_fetch_is_retained_and_replay_never_contacts_provider(
    workspace_tmp_path, monkeypatch
):
    m = api()
    instance, binding, calls, _ = service(workspace_tmp_path, monkeypatch)
    request = m.LiteratureRequest(
        operation="fetch", provider="pubmed", identifier="123"
    )
    result = instance.execute(
        request, request_id="fetch-one", context_id="ctx", binding=binding
    )
    assert result["status"] == "available"
    snapshot = instance.replay(result["snapshot_id"])
    assert Path(snapshot["responses"][0]["path"]).read_bytes() == PUBMED
    assert snapshot["extraction"]["access"] == "abstract"
    assert "Dexamethasone reduced" in snapshot["extraction"]["passages"][0]["text"]
    before = len(calls)
    replayed = instance.execute(
        request, request_id="fetch-one", context_id="ctx", binding=binding
    )
    assert replayed["snapshot_id"] == result["snapshot_id"]
    assert (
        replayed["query_receipt"]["disclosure_reservation_id"]
        != result["query_receipt"]["disclosure_reservation_id"]
    )
    assert instance.replay(result["snapshot_id"]) == snapshot and len(calls) == before


def test_changed_request_identity_cannot_reuse_completed_operation(
    workspace_tmp_path, monkeypatch
):
    m = api()
    instance, binding, _, _ = service(workspace_tmp_path, monkeypatch)
    instance.execute(
        m.LiteratureRequest(operation="fetch", provider="pubmed", identifier="123"),
        request_id="one",
        context_id="ctx",
        binding=binding,
    )
    with pytest.raises(ValueError, match="identity"):
        instance.execute(
            m.LiteratureRequest(operation="fetch", provider="pubmed", identifier="456"),
            request_id="one",
            context_id="ctx",
            binding=binding,
        )


@pytest.mark.parametrize("use", ["assessment", "replay"])
def test_unrelated_interrupted_operation_does_not_block_assessment_or_explicit_replay(
    workspace_tmp_path, monkeypatch, use
):
    from devbench.analysis import method_sources
    from devbench.analysis.scientific_review_holds import ReviewEntity
    from devbench.engine.workflow_operations import OperationBlocked

    m = api()
    instance, binding, _, lifecycle = service(workspace_tmp_path, monkeypatch)
    other_claim = claim().model_copy(update={"claim_id": "pending-claim"})
    other_binding = binding.model_copy(
        update={
            "claim_id": other_claim.claim_id,
            "claim_sha256": digest(other_claim.model_dump(mode="json")),
            "obligation_id": "pending-literature",
        }
    )
    lifecycle.register_entities(
        (ReviewEntity(entity_id=other_claim.claim_id, kind="claim"),)
    )
    instance.bind_claim(other_binding)
    original_hop = method_sources._fetch_hop
    network_calls = []

    def interrupted_hop(*args, **kwargs):
        network_calls.append("interrupted")
        raise KeyboardInterrupt("Synthetic interruption after transfer reservation")

    monkeypatch.setattr(method_sources, "_fetch_hop", interrupted_hop)
    request = m.LiteratureRequest(
        operation="fetch", provider="pubmed", identifier="123"
    )
    with pytest.raises(KeyboardInterrupt):
        instance.execute(
            request, request_id="interrupted", context_id="ctx", binding=other_binding
        )
    pending = instance.state()
    assert pending["operations"]["interrupted"]["status"] == "pending"
    assert pending["operations"]["interrupted"]["result"] is None
    assert (
        pending["accounting"]["requests"]
        == pending["accounting"]["unknown_transfers"]
        == 1
    )
    assert pending["accounting"]["bytes_observed"] == 0
    with instance.ledger._database() as db:
        retained = db.execute(
            "SELECT payload FROM scientific_records WHERE lineage_id='study' "
            "AND record_id LIKE 'literature-%' ORDER BY revision"
        ).fetchall()

    def successful_hop(*args, **kwargs):
        network_calls.append("successful")
        return original_hop(*args, **kwargs)

    monkeypatch.setattr(method_sources, "_fetch_hop", successful_hop)
    with pytest.raises(
        OperationBlocked, match="uncertain_reconcile_without_redispatch"
    ):
        instance.execute(
            request, request_id="interrupted", context_id="ctx", binding=other_binding
        )
    assert instance.state()["accounting"] == pending["accounting"]
    result = instance.execute(
        request, request_id="available", context_id="ctx", binding=binding
    )
    assert result["status"] == "available"
    if use == "assessment":
        assert assess(instance, binding, result["snapshot_id"])["eligible"]
        assert m.literature_use_current(
            lifecycle, "claim", binding.review_binding_sha256
        )
    else:
        replayed = instance.execute(
            m.LiteratureRequest(
                operation="replay", provider="pubmed", snapshot_id=result["snapshot_id"]
            ),
            request_id="explicit-replay",
            context_id="ctx",
            binding=binding,
        )
        assert (
            replayed["status"] == "available"
            and replayed["snapshot_id"] == result["snapshot_id"]
        )
    current = instance.state()
    assert current["operations"]["interrupted"] == pending["operations"]["interrupted"]
    assert (
        current["accounting"]["requests"] == 2
        and current["accounting"]["unknown_transfers"] == 1
    )
    assert current["accounting"]["bytes_observed"] == len(PUBMED)
    assert network_calls == ["interrupted", "successful"]
    assert not m.literature_use_current(
        lifecycle, other_claim.claim_id, other_binding.review_binding_sha256
    )
    with instance.ledger._database() as db:
        history = db.execute(
            "SELECT payload FROM scientific_records WHERE lineage_id='study' "
            "AND record_id LIKE 'literature-%' ORDER BY revision"
        ).fetchall()
    assert [row[0] for row in history[: len(retained)]] == [row[0] for row in retained]


def test_failed_retrieval_spends_shared_grant_and_keeps_claim_scoped(
    workspace_tmp_path, monkeypatch
):
    m = api()
    instance, binding, calls, lifecycle = service(
        workspace_tmp_path, monkeypatch, requests=1, retries=0
    )
    from devbench.analysis import method_sources

    monkeypatch.setattr(
        method_sources,
        "_fetch_hop",
        lambda *a, **k: (_ for _ in ()).throw(TimeoutError("unavailable")),
    )
    result = instance.execute(
        m.LiteratureRequest(operation="fetch", provider="pubmed", identifier="123"),
        request_id="one",
        context_id="ctx",
        binding=binding,
    )
    assert result["status"] == "unavailable" and result["affected_claim_ids"] == [
        "claim"
    ]
    assert instance.state()["accounting"]["requests"] == 1
    again = m.LiteratureService(
        instance.authority,
        "study",
        instance.root,
        broker=instance.broker,
        capability=instance.capability,
        limits=instance.limits,
        allow_network=True,
        data_scope="synthetic",
    )
    denied = again.execute(
        m.LiteratureRequest(operation="fetch", provider="pubmed", identifier="123"),
        request_id="two",
        context_id="ctx",
        binding=binding,
    )
    assert denied["status"] == "unavailable"
    assert again.state()["accounting"]["requests"] == 1
    assert lifecycle.state()["holds"] == {}  # No numerical or fit hold is fabricated.


def test_reservation_denial_before_transport_does_not_observe_transfer(
    workspace_tmp_path, monkeypatch
):
    instance, binding, calls, _ = service(
        workspace_tmp_path,
        monkeypatch,
        requests=1,
        retries=0,
    )
    request = api().LiteratureRequest(
        operation="fetch", provider="pubmed", identifier="123"
    )
    instance.execute(request, request_id="one", context_id="ctx", binding=binding)
    before = instance.state()["accounting"]
    denied = instance.execute(
        request, request_id="two", context_id="ctx", binding=binding
    )
    assert denied["status"] == "unavailable" and denied["failure"] == "OperationBlocked"
    assert len(calls) == 1
    assert instance.state()["accounting"] == before
    with instance.ledger._database() as db:
        rows = db.execute(
            "SELECT payload FROM scientific_records WHERE lineage_id='study' "
            "AND record_id LIKE 'literature-%'"
        ).fetchall()
    assert (
        sum(json.loads(row[0])["state"]["action"] == "observe_transfer" for row in rows)
        == 1
    )


def assess(instance, binding, snapshot_id, **changes):
    m = api()
    _, policy = instance._context("ctx")
    instance.broker.register_context(
        "reveal",
        principal_id="scientist",
        lineage_id="study",
        role="challenger",
        phase="narrative_reveal",
        policy_sha256=policy.sha256(),
        parents=("ctx",),
    )
    extraction = instance.replay(snapshot_id)["extraction"]
    value = m.LiteratureClaimAssessment(
        claim_id="claim",
        claim_sha256=binding.claim_sha256,
        evidence_sha256=binding.evidence_sha256,
        snapshot_id=snapshot_id,
        passage_ids=(extraction["passages"][0]["passage_id"],),
        relation="supports",
        source_context=binding.context,
        rationale="Attributed synthetic assessment, not an entailment certificate",
        limitations=("Only an abstract",),
        reviewer_id="challenger",
    )
    return instance.assess(
        value.model_copy(update=changes),
        binding=binding,
        claim=claim(),
        context_id="reveal",
    )


def test_claim_assessment_binds_revision_context_and_unknown_status(
    workspace_tmp_path, monkeypatch
):
    m = api()
    instance, binding, _, lifecycle = service(workspace_tmp_path, monkeypatch)
    result = instance.execute(
        m.LiteratureRequest(operation="fetch", provider="pubmed", identifier="123"),
        request_id="fetch",
        context_id="ctx",
        binding=binding,
    )
    review = assess(instance, binding, result["snapshot_id"])
    assert review["eligible"] and not review["semantic_entailment_verified"]
    assert (
        "status_coverage_unknown" in review["limitations"]
        and "abstract_only" in review["limitations"]
    )
    wrong = assess(
        instance,
        binding,
        result["snapshot_id"],
        source_context=binding.context.model_copy(update={"organism": "mouse"}),
    )
    assert not wrong["eligible"]
    with pytest.raises(ValueError, match="revision"):
        assess(instance, binding, result["snapshot_id"], claim_sha256="f" * 64)
    with pytest.raises(ValueError, match="passage"):
        assess(instance, binding, result["snapshot_id"], passage_ids=("invented",))


def test_literature_context_exact_aliases_preserve_scope_and_verification(
    workspace_tmp_path, monkeypatch
):
    m = api()
    instance, binding, _, _ = service(workspace_tmp_path, monkeypatch)
    result = instance.execute(
        m.LiteratureRequest(operation="fetch", provider="pubmed", identifier="123"),
        request_id="fetch",
        context_id="ctx",
        binding=binding,
    )
    snapshot_id = result["snapshot_id"]
    synonyms = binding.context.model_copy(
        update={
            "organism": "NCBITaxon:9606",
            "intervention": "ChEBI:41879",
            "direction": "reduced",
        }
    )
    aligned = assess(instance, binding, snapshot_id, source_context=synonyms)
    assert aligned["eligible"] and set(aligned["alignment"].values()) == {"match"}
    assert aligned["assessment"]["source_context"]["intervention"] == "ChEBI:41879"
    assert (
        "status_coverage_unknown" in aligned["limitations"]
        and not binding.requires_verified_status
    )
    wrong = synonyms.model_copy(
        update={"tissue": "lung", "intervention": "dexamethasone phosphate"}
    )
    misaligned = assess(instance, binding, snapshot_id, source_context=wrong)
    assert not misaligned["eligible"]
    assert (
        misaligned["alignment"]["tissue"]
        == misaligned["alignment"]["intervention"]
        == "mismatch"
    )


def test_literature_pages_bind_same_document_revision_across_page_keys(
    workspace_tmp_path, monkeypatch
):
    m = api()
    first_xml = PUBMED.replace(
        b"</Abstract>",
        b'<AbstractText Label="METHODS">First methods.</AbstractText></Abstract>',
    )
    responses = {"/entrez/eutils/efetch.fcgi?db=pubmed&id=123&retmode=xml": first_xml}
    instance, binding, calls, _ = service(
        workspace_tmp_path, monkeypatch, responses, max_passages=1
    )
    args = {"operation": "fetch", "provider": "pubmed", "identifier": "123"}
    first = instance.execute(
        m.LiteratureRequest(**args),
        request_id="first",
        context_id="ctx",
        binding=binding,
    )
    second = instance.execute(
        m.LiteratureRequest(**args, passage_offset=1),
        request_id="second",
        context_id="ctx",
        binding=binding,
    )
    assert first["extraction"]["passages"][0]["text"].startswith("Dexamethasone")
    assert [p["text"] for p in second["extraction"]["passages"]] == ["First methods."]
    first_id = first["snapshot_id"]
    assert (
        instance.replay(first_id, require_current=True)["extraction"]
        == first["extraction"]
    )
    assert (
        instance.replay(second["snapshot_id"], require_current=True)["extraction"]
        == second["extraction"]
    )
    responses[calls[0][1]] = first_xml.replace(b"First methods.", b"Updated methods.")
    instance.execute(
        m.LiteratureRequest(**args, section_filter="methods"),
        request_id="changed",
        context_id="ctx",
        binding=binding,
    )
    assert instance.replay(first_id)["extraction"] == first["extraction"]
    with pytest.raises(ValueError, match="source snapshot is stale"):
        instance.replay(first_id, require_current=True)


def test_changed_source_and_retraction_invalidate_only_dependent_claim(
    workspace_tmp_path, monkeypatch
):
    m = api()
    instance, binding, _, lifecycle = service(workspace_tmp_path, monkeypatch)
    request = m.LiteratureRequest(
        operation="fetch", provider="pubmed", identifier="123"
    )
    result = instance.execute(
        request, request_id="one", context_id="ctx", binding=binding
    )
    assert assess(instance, binding, result["snapshot_id"])["eligible"]
    assert m.literature_use_current(lifecycle, "claim", binding.review_binding_sha256)
    from devbench.analysis import method_sources

    updated = PUBMED.replace(
        b"</MedlineCitation>",
        b'<CommentsCorrectionsList><CommentsCorrections RefType="RetractionIn">'
        b"<PMID>999</PMID></CommentsCorrections></CommentsCorrectionsList>"
        b"</MedlineCitation>",
    )
    monkeypatch.setattr(
        method_sources, "_fetch_hop", lambda *a, **k: (None, updated, "application/xml")
    )
    newer = instance.execute(
        request, request_id="two", context_id="ctx", binding=binding
    )
    assert not m.literature_use_current(
        lifecycle, "claim", binding.review_binding_sha256
    )
    assert (
        instance.replay(result["snapshot_id"])["responses"][0]["content_sha256"]
        == hashlib.sha256(PUBMED).hexdigest()
    )
    with pytest.raises(ValueError, match="stale"):
        instance.replay(result["snapshot_id"], require_current=True)
    assert not assess(instance, binding, newer["snapshot_id"])["eligible"]
    assert lifecycle.state()["holds"] == {}


def active_fixture(
    tmp_path,
    monkeypatch,
    *,
    unavailable=False,
    scientific_obligations=False,
    duplicate_claim_handle=False,
):
    from devbench.analysis import method_sources
    from devbench.analysis.scientific_review import (
        ActiveReviewPacket,
        attach_literature_review,
    )
    from devbench.analysis.scientific_review_holds import (
        ResolutionPredicate,
        ReviewEntity,
        ReviewHoldLifecycle,
        ReviewObligation,
        obligations_digest,
    )
    from tests.test_active_challenger_queries import packet

    m = api()
    ledger, broker, numerical, value = packet(tmp_path / "authority")
    policy = numerical.descriptor().policy_sha256
    broker.register_context(
        "reveal",
        principal_id="scientist",
        lineage_id="study",
        role="challenger",
        phase="narrative_reveal",
        policy_sha256=policy,
        parents=("ctx",),
    )
    broker.register_context(
        "controller",
        principal_id="controller",
        lineage_id="study",
        role="controller",
        phase="analysis",
        policy_sha256=policy,
    )
    lifecycle = ReviewHoldLifecycle(ledger.scientific, "study")
    lifecycle.register_entities((ReviewEntity(entity_id="claim", kind="claim"),))
    predicate = (
        ResolutionPredicate(
            kind="CounterevidenceResolved",
            owner_id="challenger",
            evidence_required=("family-source",),
            resumption_condition="Assess the exact current evidence",
        )
        if scientific_obligations
        else None
    )
    obligations = (
        ReviewObligation(
            obligation_id="donor_sensitivity",
            entity_id="claim",
            stage="claim",
            predicate=predicate,
        ),
        ReviewObligation(
            obligation_id="literature",
            entity_id="claim",
            stage="claim",
            predicate=predicate,
        ),
    )
    raw = value.model_dump(mode="json")
    raw["validation_plan"]["checks"].append("literature")
    raw["claims"][0]["required_checks"].append("literature")
    raw["binding"]["obligations_sha256"] = obligations_digest(obligations)
    raw["binding"]["claim_sha256"] = digest(
        {key: raw[key] for key in ("controller_narrative", "validation_plan", "claims")}
    )
    value = ActiveReviewPacket.model_validate_json(json.dumps(raw))
    monkeypatch.setattr(m.LiteratureCapability, "verify", lambda self: self.identity())
    calls = []

    def hop(host, target, deadline, maximum, **kwargs):
        calls.append(target)
        if unavailable:
            raise TimeoutError("synthetic retrieval failure")
        if "esearch" in target:
            return (
                None,
                b'{"esearchresult":{"count":"1","idlist":["123"],"retstart":"0"}}',
                "application/json",
            )
        return None, PUBMED, "application/xml"

    monkeypatch.setattr(method_sources, "_fetch_hop", hop)
    instance = m.LiteratureService(
        ledger.scientific,
        "study",
        tmp_path / "literature",
        broker=broker,
        capability=m.LiteratureCapability(plugin_root=str(tmp_path / "plugin")),
        limits=m.LiteratureLimits(retries=0),
        allow_network=True,
        data_scope="synthetic",
    )
    binding = m.LiteratureClaimBinding(
        claim_id="claim",
        claim_sha256=digest(value.claims[0].model_dump(mode="json")),
        evidence_sha256=value.binding.evidence_sha256,
        review_binding_sha256=value.binding.sha256(),
        obligation_id="literature",
        context=m.BiologicalContext(
            organism="human",
            tissue="airway",
            intervention="dexamethasone",
            direction="increased",
            uncertainty="observational",
        ),
    )
    entitlement = m.EntitledLiteratureQuery("lit", instance, "ctx", (binding,))
    entitlements = (
        (entitlement, m.EntitledLiteratureQuery("lit-two", instance, "ctx", (binding,)))
        if duplicate_claim_handle
        else (entitlement,)
    )
    value, handles = attach_literature_review(
        value,
        entitlements,
        controller_literature=(
            {"pmid": "987654321", "preferred_search": "controller-chosen-secret-query"},
        ),
    )
    lifecycle.register_obligations(obligations, binding=value.binding)
    return lifecycle, broker, numerical, value, handles, calls


class LiteratureSelector:
    """Drive actual metered phases with independently retrieved synthetic evidence."""

    def __init__(
        self, binding, *, fetch_before_reveal=True, assessment_mode="eligible"
    ):
        self.binding, self.fetch_before_reveal, self.assessment_mode = (
            binding,
            fetch_before_reveal,
            assessment_mode,
        )
        self.contexts = []

    def configuration(self):
        return {"backend": "recorded_scientific_review_fixture"}

    def select(self, context, output, **kwargs):
        from copy import deepcopy

        from devbench.analysis.episode_agent import Selection

        self.contexts.append(context)
        pages = context["literature_results"]
        if not pages:
            action = {
                "kind": "query_literature",
                "handle_id": "lit",
                "claim_id": "claim",
                "request": {
                    "operation": "search",
                    "provider": "pubmed",
                    "query": "airway dexamethasone contradictory findings",
                },
            }
        elif len(pages) == 1 and (
            self.fetch_before_reveal or context["phase"] == "narrative_reveal"
        ):
            action = {
                "kind": "query_literature",
                "handle_id": "lit",
                "claim_id": "claim",
                "request": {
                    "operation": "fetch",
                    "provider": "pubmed",
                    "identifier": "123",
                },
            }
        elif not context["query_results"]:
            action = {
                "kind": "query_evidence",
                "handle_id": "family-handle",
                "query": {"target_id": "family", "mode": "family_summary"},
            }
        else:
            action = {
                "kind": "scientific_review",
                "outcome": "completed",
                "rationale": "Assess the retained counterevidence",
                "covered_obligation_ids": [
                    item["obligation_id"] for item in context["obligations"]
                ],
                "objections": [],
            }
            if (
                context["phase"] == "narrative_reveal"
                and self.assessment_mode != "omitted"
            ):
                source = pages[-1]["page"]
                support = (
                    api()
                    .LiteratureClaimAssessment(
                        claim_id=self.binding.claim_id,
                        claim_sha256=self.binding.claim_sha256,
                        evidence_sha256=self.binding.evidence_sha256,
                        snapshot_id=source["snapshot_id"],
                        passage_ids=(
                            source["extraction"]["passages"][0]["passage_id"],
                        ),
                        relation="supports",
                        source_context=self.binding.context,
                        rationale=(
                            "Attributed synthetic support; no entailment certificate"
                        ),
                        limitations=("Only an abstract",),
                        reviewer_id="challenger",
                    )
                    .model_dump(mode="json")
                )
                bad = deepcopy(support)
                if self.assessment_mode == "misaligned":
                    support["source_context"]["organism"] = "mouse"
                elif self.assessment_mode == "insufficient":
                    support["relation"] = "insufficient"
                action["literature_assessments"] = [support]
                if self.assessment_mode.startswith("duplicate"):
                    bad["relation"] = "insufficient"
                    action["literature_assessments"] = (
                        [support, bad]
                        if self.assessment_mode == "duplicate_last_bad"
                        else [bad, support]
                    )
                if self.assessment_mode == "eligible":
                    action["resolution_assessments"] = [
                        {
                            "obligation_id": item["obligation_id"],
                            "binding_sha256": self.binding.review_binding_sha256,
                            "predicate_sha256": item["predicate_sha256"],
                            "satisfied": True,
                            "rationale": (
                                "Explicit attributed decision on the retained evidence"
                            ),
                            "evidence_ids": ["family-source"],
                        }
                        for item in context["obligations"]
                        if item["predicate"] is not None
                    ]
        return Selection(action, {"tokens": 5, "model_sessions_attempted": 0})


def run_literature_review(tmp_path, fixture, selector):
    from devbench.analysis.scientific_review_agent import request_active_review

    lifecycle, broker, numerical, value, handles, _ = fixture
    return request_active_review(
        tmp_path / "review",
        value,
        lifecycle=lifecycle,
        cycle_id="review",
        reviewer_id="challenger",
        selector=selector,
        broker=broker,
        context_id="ctx",
        reveal_context_id="reveal",
        controller_context_id="controller",
        query_handles=(numerical,),
        literature_handles=handles,
    )


def test_challenger_can_recover_from_premature_family_assessment(
    workspace_tmp_path,
    monkeypatch,
):
    from devbench.analysis.episode_agent import Selection

    fixture = active_fixture(workspace_tmp_path, monkeypatch)

    class Selector(LiteratureSelector):
        def select(self, context, output, **kwargs):
            if not self.contexts:
                self.contexts.append(context)
                return Selection(
                    {
                        "action": {
                            "kind": "scientific_review",
                            "outcome": "completed",
                            "rationale": (
                                "An early assessment cannot establish complete "
                                "family coverage"
                            ),
                            "covered_obligation_ids": [],
                            "objections": [],
                            "resolution_assessments": None,
                        }
                    },
                    {"tokens": 5},
                )
            return super().select(context, output, **kwargs)

    selector = Selector(fixture[4][0].bindings[0])
    result = run_literature_review(workspace_tmp_path, fixture, selector)
    assert result["status"] == "completed", result.get("detail")
    assert result["selection_feedback"][0]["reason"] == "complete_family_query_required"
    assert (
        selector.contexts[1]["selection_feedback"]["reason"]
        == "complete_family_query_required"
    )
    assert result["accounting"]["queries"] == 3


def test_challenger_stops_after_repeating_rejected_action(
    workspace_tmp_path,
    monkeypatch,
):
    from devbench.analysis.episode_agent import Selection

    fixture = active_fixture(workspace_tmp_path, monkeypatch)

    class Selector:
        def configuration(self):
            return {"backend": "recorded_scientific_review_fixture"}

        def select(self, context, output, **kwargs):
            return Selection(
                {
                    "kind": "scientific_review",
                    "outcome": "completed",
                    "rationale": "No counterevidence query",
                    "covered_obligation_ids": [],
                    "objections": [],
                },
                {"tokens": 5},
            )

    result = run_literature_review(workspace_tmp_path, fixture, Selector())
    assert result["status"] == "unavailable"
    assert result["detail"] == "duplicate_rejected_review_action"
    assert (
        result["accounting"]["invocations"] == 2
        and result["accounting"]["queries"] == 0
    )


def run_literature_rereview(tmp_path, fixture, selector, *, cycle_id):
    from devbench.analysis.scientific_review import ActiveReviewPacket
    from devbench.analysis.scientific_review_agent import (
        EntitledReviewQuery,
        request_active_review,
    )

    lifecycle, broker, numerical, packet, handles, _ = fixture
    context_id, reveal_id = cycle_id + "-initial", cycle_id + "-reveal"
    policy = numerical.descriptor().policy_sha256
    broker.register_context(
        context_id,
        principal_id=cycle_id + "-scientist",
        lineage_id="study",
        role="challenger",
        phase="publication_initial",
        policy_sha256=policy,
    )
    broker.register_context(
        reveal_id,
        principal_id=cycle_id + "-scientist",
        lineage_id="study",
        role="challenger",
        phase="narrative_reveal",
        policy_sha256=policy,
        parents=(context_id,),
    )
    numerical = EntitledReviewQuery(
        "family-handle",
        numerical.result,
        numerical.bindings,
        broker.delivery(context_id, numerical.delivery.source_id),
    )
    literature = api().EntitledLiteratureQuery(
        "lit", handles[0].service, context_id, handles[0].bindings
    )
    raw = packet.model_dump(mode="json")
    raw["query_handles"] = [numerical.descriptor().model_dump(mode="json")]
    raw["literature_handles"] = [literature.descriptor().model_dump(mode="json")]
    exposure = {
        "family-handle": broker.exposure(context_id, numerical.delivery.source_id),
        "lit": broker.exposure(context_id, literature.descriptor().source_id),
    }
    assert exposure == packet.prior_exposure
    revised = ActiveReviewPacket.model_validate_json(json.dumps(raw))
    assert revised.binding == packet.binding
    return request_active_review(
        tmp_path / cycle_id,
        revised,
        lifecycle=lifecycle,
        cycle_id=cycle_id,
        reviewer_id="challenger",
        selector=selector,
        broker=broker,
        context_id=context_id,
        reveal_context_id=reveal_id,
        controller_context_id="controller",
        query_handles=(numerical,),
        literature_handles=(literature,),
    )


def test_rereview_rejection_invalidates_prior_external_satisfaction_and_retains_history(
    workspace_tmp_path, monkeypatch
):
    from devbench.analysis.episode_agent import Selection
    from devharness_core.provenance import verify_execution

    fixture = active_fixture(
        workspace_tmp_path,
        monkeypatch,
        scientific_obligations=True,
    )
    lifecycle, _, _, packet, handles, _ = fixture
    binding = handles[0].bindings[0]
    accepted = run_literature_review(
        workspace_tmp_path,
        fixture,
        LiteratureSelector(binding),
    )
    assert accepted["status"] == "completed" and accepted["blocked_entity_ids"] == []
    before = lifecycle.state()
    prior = before["obligations"][packet.binding.sha256()]
    assert prior["literature"]["satisfied"] and prior["donor_sensitivity"]["satisfied"]
    with lifecycle.ledger._database() as db:
        history = db.execute(
            "SELECT payload FROM scientific_records WHERE lineage_id='study' "
            "ORDER BY revision"
        ).fetchall()

    class RejectLiterature(LiteratureSelector):
        def select(self, context, output, **kwargs):
            selected = super().select(context, output, **kwargs)
            if context["phase"] == "narrative_reveal":
                obligation = next(
                    item
                    for item in context["obligations"]
                    if item["obligation_id"] == "literature"
                )
                selected.action["resolution_assessments"] = [
                    {
                        "obligation_id": "literature",
                        "binding_sha256": packet.binding.sha256(),
                        "predicate_sha256": obligation["predicate_sha256"],
                        "satisfied": False,
                        "rationale": (
                            "The external obligation is no longer scientifically "
                            "satisfied"
                        ),
                        "evidence_ids": ["family-source"],
                    }
                ]
            return Selection(selected.action, selected.evidence)

    rejected = run_literature_rereview(
        workspace_tmp_path,
        fixture,
        RejectLiterature(binding, assessment_mode="omitted"),
        cycle_id="rereview",
    )
    assert rejected["status"] == "completed" and rejected["blocked_entity_ids"] == [
        "claim"
    ], rejected
    current = lifecycle.state()
    obligations = current["obligations"][packet.binding.sha256()]
    assert (
        obligations["literature"]["satisfied"] is False
        and obligations["literature"]["proof"] is None
    )
    assert obligations["donor_sensitivity"] == prior["donor_sensitivity"]
    assert current["cycles"]["review"] == before["cycles"]["review"]
    assert (
        lifecycle.review_eligibility(
            packet.binding, digest("publication-basis"), entity_id="claim"
        ).narrative
        == "held"
    )
    with lifecycle.ledger._database() as db:
        retained = db.execute(
            "SELECT payload FROM scientific_records WHERE lineage_id='study' "
            "ORDER BY revision"
        ).fetchall()
    assert [row[0] for row in retained[: len(history)]] == [row[0] for row in history]
    assert verify_execution(
        Path(accepted["receipt"]["manifest_path"]), accepted["receipt"]["root_hash"]
    )["valid"]
    restored = run_literature_rereview(
        workspace_tmp_path, fixture, LiteratureSelector(binding), cycle_id="restored"
    )
    assert restored["status"] == "completed" and restored["blocked_entity_ids"] == []
    assert lifecycle.state()["cycles"]["rereview"] == current["cycles"]["rereview"]


@pytest.mark.parametrize(
    "mode",
    ["supported", "omitted", "retracted_direct", "wrong_binding", "wrong_predicate"],
)
def test_rereview_rejection_reopens_previously_resolved_claim_hold(
    workspace_tmp_path, monkeypatch, mode
):
    from devbench.analysis.scientific_review_holds import ResolutionPredicate

    fixture = active_fixture(
        workspace_tmp_path,
        monkeypatch,
        scientific_obligations=True,
    )
    lifecycle, _, _, packet, handles, _ = fixture
    binding = handles[0].bindings[0]
    reviewed = run_literature_review(
        workspace_tmp_path,
        fixture,
        LiteratureSelector(binding),
    )
    assert reviewed["status"] == "completed"
    previous = lifecycle.state()["cycles"]["review"]
    lifecycle.open_cycle(
        "hold-cycle",
        "publication",
        packet.binding,
        reviewer_id="challenger",
        context_id="old-hold-context",
        narrative_sha256=previous["narrative_sha256"],
        validation_plan_sha256=previous["validation_plan_sha256"],
        entity_ids=("claim",),
        evidence_ids=("family-source",),
        entitled_handle_ids=("lit",),
    )
    predicate = ResolutionPredicate(
        kind="CounterevidenceResolved",
        owner_id="challenger",
        evidence_required=("reviewed-article",),
        resumption_condition="Assess applicability of the article",
    )
    held = lifecycle.admit_objection(
        "hold-cycle",
        {
            "category": "OVERCLAIM",
            "affected_entity_ids": ["claim"],
            "evidence_ids": ["family-source"],
            "requested_resolution": "Assess applicable external evidence",
            "severity": "material",
            "necessity": (
                "The numerical finding alone does not support this external "
                "interpretation"
            ),
            "predicate": predicate.model_dump(mode="json"),
        },
    )
    article = reviewed["literature_receipts"][-1]
    lifecycle.record_query_evidence(
        "hold-cycle",
        "reviewed-article",
        digest(article),
        relevant_entity_ids=("claim",),
        source_handle_id="lit",
        content_identity_sha256=article["content_identity_sha256"],
    )
    lifecycle.record_initial_assessment(
        "hold-cycle", assessment_sha256=digest("independent hold assessment")
    )
    lifecycle.reveal_narrative(
        "hold-cycle",
        narrative_sha256=previous["narrative_sha256"],
        validation_plan_sha256=previous["validation_plan_sha256"],
    )
    decision = {
        "hold_id": held["hold_id"],
        "binding_sha256": packet.binding.sha256(),
        "predicate_sha256": predicate.sha256(),
        "satisfied": True,
        "rationale": "The reviewed article satisfies this predicate",
        "evidence_ids": ["reviewed-article"],
    }
    content = {"resolution_assessments": [decision]}
    lifecycle.complete_cycle(
        "hold-cycle",
        assessment_sha256=digest(content),
        covered_obligation_ids=("donor_sensitivity", "literature"),
        assessment_content=content,
    )
    lifecycle.resolve_scientific(
        held["hold_id"],
        cycle_id="hold-cycle",
        evidence_ids=("reviewed-article",),
        assessment_sha256=digest(content),
    )
    before = lifecycle.state()
    assert before["holds"][held["hold_id"]]["status"] == "resolved"
    rejected = {
        **decision,
        "satisfied": False,
        "evidence_ids": ["family-source"],
        "rationale": "This scientific predicate is no longer satisfied",
    }
    if mode == "wrong_binding":
        rejected["binding_sha256"] = "0" * 64
    elif mode == "wrong_predicate":
        rejected["predicate_sha256"] = "0" * 64

    class RejectHold(LiteratureSelector):
        def select(self, context, output, **kwargs):
            selected = super().select(context, output, **kwargs)
            if context["phase"] == "narrative_reveal":
                selected.action.setdefault("resolution_assessments", []).append(
                    rejected
                )
            return selected

    if mode == "retracted_direct":
        retrieve_cross_article_notice(
            handles[0].service, binding, lifecycle, monkeypatch
        )
        assert not api().literature_use_current(
            lifecycle, "claim", packet.binding.sha256()
        )
        lifecycle.open_cycle(
            "reopen-hold",
            "publication",
            packet.binding,
            reviewer_id="challenger",
            context_id="negative-hold-context",
            narrative_sha256=previous["narrative_sha256"],
            validation_plan_sha256=previous["validation_plan_sha256"],
            entity_ids=("claim",),
            evidence_ids=("family-source",),
        )
        lifecycle.record_initial_assessment(
            "reopen-hold", assessment_sha256=digest("negative hold assessment")
        )
        lifecycle.reveal_narrative(
            "reopen-hold",
            narrative_sha256=previous["narrative_sha256"],
            validation_plan_sha256=previous["validation_plan_sha256"],
        )
        rejection = {"resolution_assessments": [rejected]}
        cycle = lifecycle.complete_cycle(
            "reopen-hold",
            assessment_sha256=digest(rejection),
            covered_obligation_ids=("donor_sensitivity", "literature"),
            assessment_content=rejection,
        )
        result = {
            "status": cycle["phase"],
            "blocked_entity_ids": list(
                lifecycle.blocking_entities(
                    packet.binding, touchpoint="publication", entity_ids=("claim",)
                )
            ),
        }
    else:
        result = run_literature_rereview(
            workspace_tmp_path,
            fixture,
            RejectHold(
                binding,
                assessment_mode="eligible" if mode == "supported" else "omitted",
            ),
            cycle_id="reopen-hold",
        )
    if mode in {"wrong_binding", "wrong_predicate"}:
        assert result["status"] == "failed"
        assert (
            lifecycle.state()["holds"][held["hold_id"]]
            == before["holds"][held["hold_id"]]
        )
        return
    assert result["status"] == "completed" and result["blocked_entity_ids"] == [
        "claim"
    ], result
    after = lifecycle.state()
    assert after["holds"][held["hold_id"]]["status"] == "open"
    assert after["holds"][held["hold_id"]]["resolution"] is None
    assert after["cycles"]["hold-cycle"] == before["cycles"]["hold-cycle"]
    assert all(
        item["satisfied"]
        for item in after["obligations"][packet.binding.sha256()].values()
    )


def retrieve_cross_article_notice(
    instance, binding, lifecycle, monkeypatch, *, relation="RetractionOf"
):
    from devbench.analysis import method_sources
    from devbench.analysis.scientific_review_holds import ReviewEntity

    lifecycle.register_entities((ReviewEntity(entity_id="notice-claim", kind="claim"),))
    notice_binding = binding.model_copy(
        update={"claim_id": "notice-claim", "obligation_id": "notice-literature"}
    )
    instance.bind_claim(notice_binding)
    notice = (
        PUBMED.replace(b">123<", b">999<")
        .replace(b"10.1000/test", b"10.1000/notice")
        .replace(
            b"</MedlineCitation>",
            (
                '<CommentsCorrectionsList><CommentsCorrections RefType="'
                + relation
                + '"><PMID>123</PMID>'
                "<RefSource>Exact notice source</RefSource></CommentsCorrections>"
                "</CommentsCorrectionsList></MedlineCitation>"
            ).encode(),
        )
    )
    monkeypatch.setattr(
        method_sources,
        "_fetch_hop",
        lambda *args, **kwargs: (None, notice, "application/xml"),
    )
    return instance.execute(
        api().LiteratureRequest(operation="fetch", provider="pubmed", identifier="999"),
        request_id="cross-article-notice",
        context_id="controller",
        binding=notice_binding,
    )


@pytest.mark.parametrize("change", ["RetractionOf", "ErratumFor", "fence"])
def test_completion_rechecks_current_literature_after_attributed_assessment(
    workspace_tmp_path, monkeypatch, change
):
    fixture = active_fixture(
        workspace_tmp_path,
        monkeypatch,
        scientific_obligations=True,
    )
    lifecycle, _, _, value, handles, _ = fixture
    instance, binding = handles[0].service, handles[0].bindings[0]
    original = instance.assess
    accepted = []

    def interleave(*args, **kwargs):
        assessed = original(*args, **kwargs)
        accepted.append(assessed)
        if change == "fence":
            from tests.test_scientific_authority_migration import _binding

            with instance.ledger._database() as db:
                revision = instance.authority._revision(db, "study")
            instance.authority.commit(
                "study",
                expected_revision=revision,
                record={**_binding(), "record_id": "binding-b"},
            )
        else:
            retrieve_cross_article_notice(
                instance, binding, lifecycle, monkeypatch, relation=change
            )
        return assessed

    monkeypatch.setattr(instance, "assess", interleave)
    result = run_literature_review(
        workspace_tmp_path,
        fixture,
        LiteratureSelector(binding),
    )
    assert accepted[0]["eligible"] and not api().literature_use_current(
        lifecycle, "claim", value.binding.sha256()
    )
    assert result["status"] != "completed", result
    state = lifecycle.state()
    assert state["cycles"]["review"]["phase"] in {"failed", "unavailable"}
    assert not any(
        item["satisfied"]
        for item in state["obligations"][value.binding.sha256()].values()
    )
    assert result["literature_assessments"][0] == accepted[0]
    assert (
        instance.replay(accepted[0]["assessment"]["snapshot_id"])["extraction"]
        is not None
    )


def test_completion_and_obligation_resolution_share_one_transaction(
    workspace_tmp_path, monkeypatch
):
    from devbench.engine.workflow_operations import OperationBlocked

    fixture = active_fixture(
        workspace_tmp_path,
        monkeypatch,
        scientific_obligations=True,
    )
    lifecycle, _, _, value, handles, _ = fixture
    original = lifecycle.assess_obligation
    connections = []

    def resolve(obligation_id, *args, **kwargs):
        connections.append(kwargs.get("connection"))
        if obligation_id == "literature":
            raise OperationBlocked("synthetic_late_resolution_failure")
        return original(obligation_id, *args, **kwargs)

    monkeypatch.setattr(lifecycle, "assess_obligation", resolve)
    result = run_literature_review(
        workspace_tmp_path, fixture, LiteratureSelector(handles[0].bindings[0])
    )
    state = lifecycle.state()
    assert result["status"] == "unavailable"
    assert state["cycles"]["review"]["phase"] == "unavailable"
    assert not any(
        item["satisfied"]
        for item in state["obligations"][value.binding.sha256()].values()
    )
    assert (
        len(connections) == 2
        and connections[0] is connections[1]
        and connections[0] is not None
    )


def test_late_notice_blocks_external_resolution_but_not_numerical_obligation(
    workspace_tmp_path, monkeypatch
):
    from devbench.engine.workflow_operations import OperationBlocked

    fixture = active_fixture(
        workspace_tmp_path,
        monkeypatch,
        scientific_obligations=True,
    )
    lifecycle, _, _, packet, handles, _ = fixture
    assert (
        run_literature_review(
            workspace_tmp_path, fixture, LiteratureSelector(handles[0].bindings[0])
        )["status"]
        == "completed"
    )
    cycle = lifecycle.state()["cycles"]["review"]
    retrieve_cross_article_notice(
        handles[0].service, handles[0].bindings[0], lifecycle, monkeypatch
    )
    options = {
        "cycle_id": "review",
        "evidence_ids": ("family-source",),
        "assessment_sha256": cycle["assessment_sha256"],
    }
    with pytest.raises(OperationBlocked, match="literature"):
        lifecycle.assess_obligation("literature", packet.binding, **options)
    assert lifecycle.assess_obligation("donor_sensitivity", packet.binding, **options)[
        "satisfied"
    ]


def test_rejecting_unrelated_obligation_does_not_exempt_external_claim(
    workspace_tmp_path, monkeypatch
):
    from devbench.engine.workflow_operations import OperationBlocked

    fixture = active_fixture(
        workspace_tmp_path,
        monkeypatch,
        scientific_obligations=True,
    )
    lifecycle, _, _, packet, handles, _ = fixture
    assert (
        run_literature_review(
            workspace_tmp_path, fixture, LiteratureSelector(handles[0].bindings[0])
        )["status"]
        == "completed"
    )
    previous = lifecycle.state()["cycles"]["review"]
    retrieve_cross_article_notice(
        handles[0].service, handles[0].bindings[0], lifecycle, monkeypatch
    )
    lifecycle.open_cycle(
        "second",
        "publication",
        packet.binding,
        reviewer_id="challenger",
        context_id="second-context",
        narrative_sha256=previous["narrative_sha256"],
        validation_plan_sha256=previous["validation_plan_sha256"],
        entity_ids=("claim",),
        evidence_ids=("family-source",),
    )
    lifecycle.record_initial_assessment(
        "second", assessment_sha256=digest("independent")
    )
    lifecycle.reveal_narrative(
        "second",
        narrative_sha256=previous["narrative_sha256"],
        validation_plan_sha256=previous["validation_plan_sha256"],
    )
    rejection = {**previous["resolution_assessments"][0], "satisfied": False}
    assert rejection["obligation_id"] == "donor_sensitivity"
    content = {"resolution_assessments": [rejection]}
    with pytest.raises(OperationBlocked, match="literature"):
        lifecycle.complete_cycle(
            "second",
            assessment_sha256=digest(content),
            covered_obligation_ids=("donor_sensitivity", "literature"),
            assessment_content=content,
        )
    assert lifecycle.state()["cycles"]["second"]["phase"] == "narrative_revealed"


def test_late_notice_cannot_resolve_scientific_claim_hold(
    workspace_tmp_path,
    monkeypatch,
):
    from devbench.analysis.scientific_review_holds import ResolutionPredicate
    from devbench.engine.workflow_operations import OperationBlocked

    fixture = active_fixture(
        workspace_tmp_path,
        monkeypatch,
        scientific_obligations=True,
    )
    lifecycle, _, _, packet, handles, _ = fixture
    reviewed = run_literature_review(
        workspace_tmp_path, fixture, LiteratureSelector(handles[0].bindings[0])
    )
    assert reviewed["status"] == "completed"
    previous = lifecycle.state()["cycles"]["review"]
    lifecycle.open_cycle(
        "hold-review",
        "publication",
        packet.binding,
        reviewer_id="challenger",
        context_id="hold-context",
        narrative_sha256=previous["narrative_sha256"],
        validation_plan_sha256=previous["validation_plan_sha256"],
        entity_ids=("claim",),
        evidence_ids=("family-source",),
        entitled_handle_ids=("lit",),
    )
    predicate = ResolutionPredicate(
        kind="CounterevidenceResolved",
        owner_id="challenger",
        evidence_required=("new-article",),
        resumption_condition="Assess the applicable article",
    )
    held = lifecycle.admit_objection(
        "hold-review",
        {
            "category": "OVERCLAIM",
            "affected_entity_ids": ["claim"],
            "evidence_ids": ["family-source"],
            "requested_resolution": "Assess applicable external evidence",
            "severity": "material",
            "necessity": (
                "Numerical results alone do not establish this external interpretation"
            ),
            "predicate": predicate.model_dump(mode="json"),
        },
    )
    assert held["status"] == "held"
    article = reviewed["literature_receipts"][-1]
    lifecycle.record_query_evidence(
        "hold-review",
        "new-article",
        digest(article),
        relevant_entity_ids=("claim",),
        source_handle_id="lit",
        content_identity_sha256=article["content_identity_sha256"],
    )
    lifecycle.record_initial_assessment(
        "hold-review", assessment_sha256=digest("independent")
    )
    lifecycle.reveal_narrative(
        "hold-review",
        narrative_sha256=previous["narrative_sha256"],
        validation_plan_sha256=previous["validation_plan_sha256"],
    )
    content = {
        "resolution_assessments": [
            {
                "hold_id": held["hold_id"],
                "binding_sha256": packet.binding.sha256(),
                "predicate_sha256": predicate.sha256(),
                "satisfied": True,
                "rationale": "Explicit assessment of the retained article",
                "evidence_ids": ["new-article"],
            }
        ]
    }
    lifecycle.complete_cycle(
        "hold-review",
        assessment_sha256=digest(content),
        covered_obligation_ids=("donor_sensitivity", "literature"),
        assessment_content=content,
    )
    retrieve_cross_article_notice(
        handles[0].service, handles[0].bindings[0], lifecycle, monkeypatch
    )
    with pytest.raises(OperationBlocked, match="literature"):
        lifecycle.resolve_scientific(
            held["hold_id"],
            cycle_id="hold-review",
            evidence_ids=("new-article",),
            assessment_sha256=digest(content),
        )
    assert lifecycle.state()["holds"][held["hold_id"]]["status"] == "open"


@pytest.mark.parametrize(
    "literature_request",
    [
        {"operation": "fetch", "provider": "pubmed", "identifier": "987654321"},
        {
            "operation": "search",
            "provider": "pubmed",
            "query": "controller-chosen-secret-query",
        },
    ],
)
def test_controller_only_literature_cannot_seed_initial_countersearch(
    workspace_tmp_path, monkeypatch, literature_request
):
    from devbench.analysis.episode_agent import Selection

    fixture = active_fixture(workspace_tmp_path, monkeypatch)

    class Selector(LiteratureSelector):
        def select(self, context, output, **kwargs):
            selection = super().select(context, output, **kwargs)
            if len(self.contexts) == 2:
                return Selection(
                    {
                        "kind": "query_literature",
                        "handle_id": "lit",
                        "claim_id": "claim",
                        "request": literature_request,
                    },
                    {"tokens": 5},
                )
            return selection

    selector = Selector(fixture[4][0].bindings[0])
    result = run_literature_review(workspace_tmp_path, fixture, selector)
    assert result["status"] == "failed" and result["reason"] == "InvalidSelection", (
        result
    )
    assert len(fixture[-1]) == 1
    assert all(item["phase"] == "evidence_first" for item in selector.contexts)


@pytest.mark.parametrize("decision", ["rejected", "hold"])
def test_explicit_external_rejection_or_hold_can_complete_without_support(
    workspace_tmp_path, monkeypatch, decision
):
    from devbench.analysis.episode_agent import Selection

    fixture = active_fixture(
        workspace_tmp_path,
        monkeypatch,
        scientific_obligations=True,
    )

    class Selector(LiteratureSelector):
        def select(self, context, output, **kwargs):
            selection = super().select(context, output, **kwargs)
            if context["phase"] == "narrative_reveal":
                action = selection.action
                if decision == "rejected":
                    action["resolution_assessments"] = [
                        {
                            "obligation_id": item["obligation_id"],
                            "binding_sha256": self.binding.review_binding_sha256,
                            "predicate_sha256": item["predicate_sha256"],
                            "satisfied": False,
                            "rationale": (
                                "Insufficient external support; retain the scoped block"
                            ),
                            "evidence_ids": ["family-source"],
                        }
                        for item in context["obligations"]
                    ]
                else:
                    action["objections"] = [
                        {
                            "category": "OVERCLAIM",
                            "affected_entity_ids": ["claim"],
                            "evidence_ids": ["family-source"],
                            "missing_obligation_ids": ["literature"],
                            "requested_resolution": (
                                "Supply current applicable external evidence"
                            ),
                            "severity": "material",
                            "necessity": (
                                "The retained abstract is insufficient for this "
                                "biological interpretation"
                            ),
                            "predicate": {
                                "kind": "CounterevidenceResolved",
                                "owner_id": "challenger",
                                "evidence_required": ["family-source"],
                                "resumption_condition": (
                                    "Assess new applicable external evidence"
                                ),
                            },
                        }
                    ]
                return Selection(action, selection.evidence)
            return selection

    result = run_literature_review(
        workspace_tmp_path,
        fixture,
        Selector(fixture[4][0].bindings[0], assessment_mode="omitted"),
    )
    assert result["status"] == "completed" and result["blocked_entity_ids"] == [
        "claim"
    ], result
    assert not fixture[0].state()["obligations"][fixture[3].binding.sha256()][
        "literature"
    ]["satisfied"]


def test_distinct_handles_cannot_mask_duplicate_claim_assessment(
    workspace_tmp_path,
    monkeypatch,
):
    from devbench.analysis.episode_agent import Selection

    fixture = active_fixture(
        workspace_tmp_path,
        monkeypatch,
        duplicate_claim_handle=True,
    )

    class Selector(LiteratureSelector):
        def select(self, context, output, **kwargs):
            selection = super().select(context, output, **kwargs)
            if context["phase"] == "evidence_first" and len(self.contexts) == 3:
                return Selection(
                    {
                        "kind": "query_literature",
                        "handle_id": "lit-two",
                        "claim_id": "claim",
                        "request": {
                            "operation": "search",
                            "provider": "pubmed",
                            "query": "independent contradictory airway evidence",
                        },
                    },
                    {"tokens": 5},
                )
            return selection

    selector = Selector(fixture[4][0].bindings[0], assessment_mode="duplicate_last_bad")
    # Keep the fetched passage visible even when a second handle returns a later
    # search page.
    original_select = selector.select

    def select(context, output, **kwargs):
        if context["phase"] == "narrative_reveal":
            context = {
                **context,
                "literature_results": sorted(
                    context["literature_results"],
                    key=lambda item: item["page"]["extraction"] is not None,
                ),
            }
        return original_select(context, output, **kwargs)

    selector.select = select
    result = run_literature_review(workspace_tmp_path, fixture, selector)
    assert result["status"] == "failed" and result["reason"] == "InvalidSelection"
    assert result["literature_assessments"] == []


def test_reveal_query_requires_initial_context_ancestry(
    workspace_tmp_path,
    monkeypatch,
):
    fixture = active_fixture(workspace_tmp_path, monkeypatch)
    handle = fixture[4][0]
    fixture[1].register_context(
        "unrelated",
        principal_id="scientist",
        lineage_id="study",
        role="challenger",
        phase="narrative_reveal",
        policy_sha256=fixture[2].descriptor().policy_sha256,
    )
    with pytest.raises(PermissionError, match="ancestry"):
        handle.query(
            {"operation": "fetch", "provider": "pubmed", "identifier": "123"},
            claim_id="claim",
            request_id="unrelated-query",
            context_id="unrelated",
        )
    assert fixture[-1] == []


def test_reveal_can_explicitly_replay_source_disclosed_to_initial_reviewer(
    workspace_tmp_path, monkeypatch
):
    from devbench.analysis.episode_agent import Selection

    fixture = active_fixture(
        workspace_tmp_path,
        monkeypatch,
        scientific_obligations=True,
    )

    class ReplayInReveal(LiteratureSelector):
        def select(self, context, output, **kwargs):
            selected = super().select(context, output, **kwargs)
            if (
                context["phase"] == "narrative_reveal"
                and len(context["literature_results"]) == 2
            ):
                snapshot = context["literature_results"][-1]["page"]["snapshot_id"]
                return Selection(
                    {
                        "kind": "query_literature",
                        "handle_id": "lit",
                        "claim_id": "claim",
                        "request": {
                            "operation": "replay",
                            "provider": "pubmed",
                            "snapshot_id": snapshot,
                        },
                    },
                    selected.evidence,
                )
            return selected

    result = run_literature_review(
        workspace_tmp_path, fixture, ReplayInReveal(fixture[4][0].bindings[0])
    )
    assert result["status"] == "completed" and result["blocked_entity_ids"] == [], (
        result
    )
    assert len(fixture[-1]) == 2 and result["accounting"]["queries"] == 4
    assert (
        result["literature_receipts"][-1]["snapshot_id"]
        == result["literature_receipts"][-2]["snapshot_id"]
    )


@pytest.mark.parametrize("phase", ["publication_initial", "narrative_reveal"])
def test_explicit_replay_cannot_borrow_unrelated_context_snapshot(
    workspace_tmp_path, monkeypatch, phase
):
    instance, binding, calls, _ = service(workspace_tmp_path, monkeypatch)
    fetched = instance.execute(
        api().LiteratureRequest(operation="fetch", provider="pubmed", identifier="123"),
        request_id="fetched",
        context_id="ctx",
        binding=binding,
    )
    _, policy = instance._context("ctx")
    instance.broker.register_context(
        "unrelated-initial",
        principal_id="other-scientist",
        lineage_id="study",
        role="challenger",
        phase="publication_initial",
        policy_sha256=policy.sha256(),
    )
    instance.broker.register_context(
        "unrelated",
        principal_id="other-scientist",
        lineage_id="study",
        role="challenger",
        phase=phase,
        policy_sha256=policy.sha256(),
        parents=("unrelated-initial",),
    )
    with pytest.raises(PermissionError, match="not issued"):
        instance.execute(
            api().LiteratureRequest(
                operation="replay",
                provider="pubmed",
                snapshot_id=fetched["snapshot_id"],
            ),
            request_id="borrowed",
            context_id="unrelated",
            binding=binding,
        )
    assert len(calls) == 1


def test_initial_reviewer_cannot_replay_controller_only_snapshot(
    workspace_tmp_path,
    monkeypatch,
):
    from devbench.analysis.episode_agent import Selection

    fixture = active_fixture(workspace_tmp_path, monkeypatch)
    instance, binding = fixture[4][0].service, fixture[4][0].bindings[0]
    instance.bind_claim(binding)
    controller_source = instance.execute(
        api().LiteratureRequest(operation="fetch", provider="pubmed", identifier="123"),
        request_id="controller-only",
        context_id="controller",
        binding=binding,
    )

    class ControllerReplay(LiteratureSelector):
        def select(self, context, output, **kwargs):
            self.contexts.append(context)
            return Selection(
                {
                    "kind": "query_literature",
                    "handle_id": "lit",
                    "claim_id": "claim",
                    "request": {
                        "operation": "replay",
                        "provider": "pubmed",
                        "snapshot_id": controller_source["snapshot_id"],
                    },
                },
                {"tokens": 1},
            )

    selector = ControllerReplay(binding)
    result = run_literature_review(workspace_tmp_path, fixture, selector)
    assert result["status"] == "failed" and result["reason"] == "PermissionError", (
        result
    )
    assert len(fixture[-1]) == 1 and all(
        item["phase"] == "evidence_first" for item in selector.contexts
    )
    assert result["literature_receipts"] == []


def test_reveal_can_fetch_and_assess_independently_found_source(
    workspace_tmp_path,
    monkeypatch,
):
    fixture = active_fixture(
        workspace_tmp_path,
        monkeypatch,
        scientific_obligations=True,
    )
    lifecycle, _, _, value, handles, calls = fixture
    selector = LiteratureSelector(handles[0].bindings[0], fetch_before_reveal=False)
    result = run_literature_review(workspace_tmp_path, fixture, selector)
    assert result["status"] == "completed", result.get("detail")
    assert result["blocked_entity_ids"] == [] and len(calls) == 2
    operations = handles[0].service.state()["operations"].values()
    assert {
        item["spec"]["context_id"]
        for item in operations
        if item["spec"]["request"]["operation"] == "fetch"
    } == {"reveal"}
    assert lifecycle.state()["obligations"][value.binding.sha256()]["literature"][
        "satisfied"
    ]
    assert all(
        "987654321" not in json.dumps(item)
        for item in selector.contexts
        if item["phase"] == "evidence_first"
    )


@pytest.mark.parametrize(
    "mode",
    [
        "omitted",
        "insufficient",
        "misaligned",
        "duplicate_last_bad",
        "duplicate_last_good",
    ],
)
def test_completion_cannot_omit_or_mask_ineligible_literature_assessment(
    workspace_tmp_path, monkeypatch, mode
):
    fixture = active_fixture(workspace_tmp_path, monkeypatch)
    selector = LiteratureSelector(fixture[4][0].bindings[0], assessment_mode=mode)
    result = run_literature_review(workspace_tmp_path, fixture, selector)
    assert result["status"] == "failed" and result["reason"] == "InvalidSelection", (
        result
    )
    state = fixture[0].state()
    assert state["cycles"]["review"]["phase"] == "failed"
    assert not state["obligations"][fixture[3].binding.sha256()]["literature"][
        "satisfied"
    ]
    assert result["accounting"]["queries"] == 3 and result["observed_tokens"] > 0


@pytest.mark.parametrize("failure", ["unavailable", "permission", "truncated"])
def test_unsuccessful_independent_search_cannot_unlock_narrative(
    workspace_tmp_path, monkeypatch, failure
):
    fixture = active_fixture(
        workspace_tmp_path, monkeypatch, unavailable=failure == "unavailable"
    )
    if failure == "permission":
        monkeypatch.setattr(
            api().LiteratureCapability,
            "verify",
            lambda self: (_ for _ in ()).throw(PermissionError("denied")),
        )
    elif failure == "truncated":
        from devbench.analysis import method_sources

        monkeypatch.setattr(
            method_sources,
            "_fetch_hop",
            lambda *args, **kwargs: (
                None,
                b'{"esearchresult":{"count":"100","idlist":["123"],"retstart":"0"}}',
                "application/json",
            ),
        )
    selector = LiteratureSelector(
        fixture[4][0].bindings[0], fetch_before_reveal=False, assessment_mode="omitted"
    )
    result = run_literature_review(workspace_tmp_path, fixture, selector)
    assert result["status"] == "failed" and result["reason"] == "InvalidSelection", (
        result
    )
    assert all(item["phase"] == "evidence_first" for item in selector.contexts)
    assert all("987654321" not in json.dumps(item) for item in selector.contexts)
    assert fixture[0].state()["cycles"]["review"]["phase"] == "failed"
    assert result["accounting"]["queries"] == 2 and result["observed_tokens"] == 15


def test_actual_challenger_countersearch_precedes_preferred_sources_and_narrative(
    workspace_tmp_path, monkeypatch
):
    from devbench.analysis.episode_agent import Selection
    from devbench.analysis.scientific_review_agent import request_active_review

    lifecycle, broker, numerical, value, handles, calls = active_fixture(
        workspace_tmp_path, monkeypatch
    )
    contexts = []

    class Selector:
        def configuration(self):
            return {"backend": "recorded_scientific_review_fixture"}

        def select(self, context, output, **kwargs):
            contexts.append(context)
            if context["phase"] == "evidence_first":
                assert "987654321" not in json.dumps(
                    context
                ) and "controller-chosen-secret-query" not in json.dumps(context)
            if len(contexts) == 1:
                action = {
                    "kind": "query_literature",
                    "handle_id": "lit",
                    "claim_id": "claim",
                    "request": {
                        "operation": "search",
                        "provider": "pubmed",
                        "query": "airway dexamethasone contradictory findings",
                    },
                }
            elif len(contexts) == 2:
                action = {
                    "kind": "query_literature",
                    "handle_id": "lit",
                    "claim_id": "claim",
                    "request": {
                        "operation": "fetch",
                        "provider": "pubmed",
                        "identifier": "123",
                    },
                }
            elif len(contexts) == 3:
                action = {
                    "kind": "query_evidence",
                    "handle_id": "family-handle",
                    "query": {"target_id": "family", "mode": "family_summary"},
                }
            else:
                action = {
                    "kind": "scientific_review",
                    "outcome": "completed",
                    "rationale": "Counterevidence limits this direction",
                    "covered_obligation_ids": [
                        item["obligation_id"] for item in context["obligations"]
                    ],
                    "objections": [],
                }
                if context["phase"] == "narrative_reveal":
                    action = (
                        LiteratureSelector(handles[0].bindings[0])
                        .select(context, output, **kwargs)
                        .action
                    )
            return Selection(action, {"tokens": 5, "model_sessions_attempted": 0})

    result = request_active_review(
        workspace_tmp_path / "review",
        value,
        lifecycle=lifecycle,
        cycle_id="review",
        reviewer_id="challenger",
        selector=Selector(),
        broker=broker,
        context_id="ctx",
        reveal_context_id="reveal",
        controller_context_id="controller",
        query_handles=(numerical,),
        literature_handles=handles,
    )
    assert result["status"] == "completed" and len(calls) == 2
    assert "987654321" in json.dumps(contexts[-1])
    assert result["accounting"]["queries"] == 3
    assert (
        contexts[2]["literature_results"][1]["page"]["extraction"]["access"]
        == "abstract"
    )
    assert result["blocked_entity_ids"] == [
        "claim"
    ]  # Retrieval is not scientific resolution.


def test_missing_independent_search_cannot_reveal_controller_sources(
    workspace_tmp_path, monkeypatch
):
    from devbench.analysis.episode_agent import Selection
    from devbench.analysis.scientific_review_agent import request_active_review

    lifecycle, broker, numerical, value, handles, _ = active_fixture(
        workspace_tmp_path, monkeypatch
    )
    contexts = []

    class Selector:
        def configuration(self):
            return {"backend": "recorded_scientific_review_fixture"}

        def select(self, context, output, **kwargs):
            contexts.append(context)
            action = (
                {
                    "kind": "query_evidence",
                    "handle_id": "family-handle",
                    "query": {"target_id": "family", "mode": "family_summary"},
                }
                if len(contexts) == 1
                else {
                    "kind": "scientific_review",
                    "outcome": "completed",
                    "rationale": "No search",
                    "covered_obligation_ids": [],
                    "objections": [],
                }
            )
            return Selection(action, {"tokens": 1})

    result = request_active_review(
        workspace_tmp_path / "review",
        value,
        lifecycle=lifecycle,
        cycle_id="review",
        reviewer_id="challenger",
        selector=Selector(),
        broker=broker,
        context_id="ctx",
        reveal_context_id="reveal",
        controller_context_id="controller",
        query_handles=(numerical,),
        literature_handles=handles,
    )
    assert result["status"] == "failed" and result["reason"] == "InvalidSelection"
    assert all(context["phase"] == "evidence_first" for context in contexts)


@pytest.mark.parametrize("part", ["raw", "snapshot"])
def test_tampered_custody_cannot_replay_deliver_or_publish(
    workspace_tmp_path,
    monkeypatch,
    part,
):
    m = api()
    instance, binding, _, lifecycle = service(workspace_tmp_path, monkeypatch)
    request = m.LiteratureRequest(
        operation="fetch", provider="pubmed", identifier="123"
    )
    result = instance.execute(
        request, request_id="one", context_id="ctx", binding=binding
    )
    assert assess(instance, binding, result["snapshot_id"])["eligible"]
    record = instance.replay(result["snapshot_id"])
    path = (
        Path(record["responses"][0]["path"])
        if part == "raw"
        else Path(instance.state()["snapshots"][result["snapshot_id"]]["path"])
    )
    path.write_bytes(b"{}")
    with pytest.raises(ValueError, match="changed"):
        instance.execute(request, request_id="one", context_id="ctx", binding=binding)
    assert not m.literature_use_current(
        lifecycle, "claim", binding.review_binding_sha256
    )


def test_identical_failure_snapshots_do_not_strand_retries(
    workspace_tmp_path,
    monkeypatch,
):
    m = api()
    instance, binding, _, _ = service(
        workspace_tmp_path,
        monkeypatch,
        requests=4,
        retries=0,
    )
    from devbench.analysis import method_sources

    monkeypatch.setattr(
        method_sources,
        "_fetch_hop",
        lambda *a, **k: (_ for _ in ()).throw(TimeoutError("same failure")),
    )
    results = [
        instance.execute(
            m.LiteratureRequest(operation="fetch", provider="pubmed", identifier="123"),
            request_id="attempt-" + str(i),
            context_id="ctx",
            binding=binding,
        )
        for i in range(3)
    ]
    assert all(item["status"] == "unavailable" for item in results)
    assert len({item["snapshot_id"] for item in results}) == 1
    assert instance.state()["accounting"]["requests"] == 3
    assert all(
        item["status"] == "completed"
        for item in instance.state()["operations"].values()
    )


def test_identical_search_and_fetch_have_stable_scientific_identity(
    workspace_tmp_path, monkeypatch
):
    m = api()
    instance, binding, _, _ = service(workspace_tmp_path, monkeypatch)
    request = m.LiteratureRequest(
        operation="fetch", provider="pubmed", identifier="123"
    )
    first = instance.execute(
        request, request_id="one", context_id="ctx", binding=binding
    )
    second = instance.execute(
        request, request_id="two", context_id="ctx", binding=binding
    )
    assert (
        first["snapshot_id"] != second["snapshot_id"]
    )  # Distinct timestamp/capture custody.
    assert (
        first["query_receipt"]["content_identity_sha256"]
        == second["query_receipt"]["content_identity_sha256"]
    )


def test_unknown_later_status_does_not_clear_known_retraction(
    workspace_tmp_path,
    monkeypatch,
):
    m = api()
    instance, binding, _, lifecycle = service(workspace_tmp_path, monkeypatch)
    from devbench.analysis import method_sources

    retracted = PUBMED.replace(
        b"</MedlineCitation>",
        b'<CommentsCorrectionsList><CommentsCorrections RefType="RetractionIn">'
        b"<PMID>999</PMID></CommentsCorrections></CommentsCorrectionsList>"
        b"</MedlineCitation>",
    )
    monkeypatch.setattr(
        method_sources,
        "_fetch_hop",
        lambda *a, **k: (None, retracted, "application/xml"),
    )
    request = m.LiteratureRequest(
        operation="fetch", provider="pubmed", identifier="123"
    )
    first = instance.execute(
        request, request_id="one", context_id="ctx", binding=binding
    )
    assert not assess(instance, binding, first["snapshot_id"])["eligible"]
    monkeypatch.setattr(
        method_sources, "_fetch_hop", lambda *a, **k: (None, PUBMED, "application/xml")
    )
    later = instance.execute(
        request, request_id="two", context_id="ctx", binding=binding
    )
    assert later["extraction"]["status"]["retraction"] == "unknown"
    assert not assess(instance, binding, later["snapshot_id"])["eligible"]
    assert instance.state()["retracted"]["pmid:123"]


def test_retraction_notice_about_another_article_does_not_poison_this_source(
    workspace_tmp_path, monkeypatch
):
    m = api()
    instance, binding, _, _ = service(workspace_tmp_path, monkeypatch)
    from devbench.analysis import method_sources

    notice = PUBMED.replace(
        b"</MedlineCitation>",
        b'<CommentsCorrectionsList><CommentsCorrections RefType="RetractionOf">'
        b"<PMID>999</PMID></CommentsCorrections></CommentsCorrectionsList>"
        b"</MedlineCitation>",
    )
    monkeypatch.setattr(
        method_sources, "_fetch_hop", lambda *a, **k: (None, notice, "application/xml")
    )
    result = instance.execute(
        m.LiteratureRequest(operation="fetch", provider="pubmed", identifier="123"),
        request_id="one",
        context_id="ctx",
        binding=binding,
    )
    assert result["extraction"]["status"]["retraction"] == "unknown"
    assert instance.state()["retracted"] == {"pmid:999": True}


def test_literature_failure_keeps_actual_broker_numerical_publication_eligible(
    workspace_tmp_path, monkeypatch
):
    from devbench.analysis.scientific_publication import (
        ReviewEligibility,
        access_from_delivery,
        build_publication_descriptor,
        fixed_capsule,
    )
    from tests.test_scientific_publication_policy import _allocation, _observed, profile

    m = api()
    instance, binding, _, _ = service(workspace_tmp_path, monkeypatch, retries=0)
    broker = instance.broker
    broker.register_source(
        "numerical",
        content_sha256="a" * 64,
        roots=(binding.evidence_sha256,),
        classification="structural",
    )
    delivery = broker.delivery("ctx", "numerical")
    before = access_from_delivery(delivery, "a" * 64)
    from devbench.analysis import method_sources

    monkeypatch.setattr(
        method_sources,
        "_fetch_hop",
        lambda *a, **k: (_ for _ in ()).throw(TimeoutError("missing source")),
    )
    result = instance.execute(
        m.LiteratureRequest(operation="fetch", provider="pubmed", identifier="123"),
        request_id="one",
        context_id="ctx",
        binding=binding,
    )
    after = access_from_delivery(delivery, "a" * 64)
    assert before == after and after.exposure == "structural"
    allocation = _allocation()
    procedure = profile("qualified_procedure_result", "exploratory_finding")
    kwargs = dict(source_sha256="a" * 64, allocation=allocation, access=after)
    draft = build_publication_descriptor(procedure, **kwargs)
    descriptor = build_publication_descriptor(
        procedure,
        **kwargs,
        review=ReviewEligibility(
            basis_sha256=draft.basis_sha256,
            design="current",
            numerical="current",
            evidence_first="held",
            narrative="held",
        ),
    )
    published = fixed_capsule(
        descriptor,
        allocation,
        (_observed(allocation, "first"), _observed(allocation, "second")),
    )
    assert result["status"] == "unavailable" and any(
        "p" in row for row in published["table"]
    )
    assert not descriptor.allow_interpretation


def pmc_fixture(
    *, retracted=False, metadata_ids=True, text="Dexamethasone reduced expression."
):
    body = (
        f'<article article-status="{"retracted" if retracted else "active"}">'
        "<front><article-meta>"
        '<article-id pub-id-type="pmc">PMC123</article-id>'
        '<article-id pub-id-type="pmid">123</article-id>'
        '<article-id pub-id-type="doi">10.1000/test</article-id>'
        "<title-group><article-title>Human airway response</article-title>"
        "</title-group>"
        f"</article-meta></front><body><p>{text}</p></body></article>"
    ).encode()
    prefix = "https://pmc-oa-opendata.s3.amazonaws.com/PMC123.1/PMC123.1.xml"
    metadata = dict(
        pmcid="PMC123",
        version=1,
        pmid="123" if metadata_ids else None,
        doi="10.1000/test" if metadata_ids else None,
        title="Human airway response",
        citation="2024",
        is_pmc_openaccess="yes",
        is_manuscript="no",
        is_historical_ocr="no",
        is_retracted="no",
        license_code="CC-BY",
        xml_url=prefix + "?md5=" + hashlib.md5(body).hexdigest(),
        pdf_url=None,
        media_urls=[],
        text_url=None,
    )
    return body, json.dumps(metadata).encode()


@pytest.mark.parametrize("metadata_ids", [True, False])
def test_pmc_body_retraction_overrides_metadata_and_preserves_aliases(
    workspace_tmp_path, monkeypatch, metadata_ids
):
    m = api()
    instance, binding, _, lifecycle = service(workspace_tmp_path, monkeypatch)
    first = instance.execute(
        m.LiteratureRequest(operation="fetch", provider="pubmed", identifier="123"),
        request_id="pubmed",
        context_id="ctx",
        binding=binding,
    )
    assert assess(instance, binding, first["snapshot_id"])["eligible"]
    xml, metadata = pmc_fixture(retracted=True, metadata_ids=metadata_ids)
    from devbench.analysis import method_sources

    monkeypatch.setattr(
        method_sources,
        "_fetch_hop",
        lambda host, target, *a, **k: (
            None,
            metadata if target.startswith("/metadata/") else xml,
            "application/json"
            if target.startswith("/metadata/")
            else "application/xml",
        ),
    )
    result = instance.execute(
        m.LiteratureRequest(
            operation="fetch", provider="pmc", identifier="PMC123", version=1
        ),
        request_id="pmc",
        context_id="ctx",
        binding=binding,
    )
    assert result["status"] == "available" and result["access"] == "full_text"
    assert len(result["responses"]) == 2
    assert not m.literature_use_current(
        lifecycle, "claim", binding.review_binding_sha256
    )
    assert not assess(instance, binding, result["snapshot_id"])["eligible"]
    assert instance.state()["retracted"]["pmid:123"]


def test_canonical_pmc_id_cannot_launder_changed_source(
    workspace_tmp_path,
    monkeypatch,
):
    m = api()
    instance, binding, _, _ = service(workspace_tmp_path, monkeypatch)
    from devbench.analysis import method_sources

    version = [pmc_fixture()]
    monkeypatch.setattr(
        method_sources,
        "_fetch_hop",
        lambda host, target, *a, **k: (
            None,
            version[0][1] if target.startswith("/metadata/") else version[0][0],
            "application/json"
            if target.startswith("/metadata/")
            else "application/xml",
        ),
    )
    low = m.LiteratureRequest(
        operation="fetch", provider="pmc", identifier="pmc123", version=1
    )
    high = m.LiteratureRequest(
        operation="fetch", provider="pmc", identifier="PMC123", version=1
    )
    assert low == high
    old = instance.execute(low, request_id="one", context_id="ctx", binding=binding)
    version[0] = pmc_fixture(text="Opposite current content.")
    instance.execute(high, request_id="two", context_id="ctx", binding=binding)
    with pytest.raises(ValueError, match="stale"):
        instance.replay(old["snapshot_id"], require_current=True)


def test_reused_source_in_new_review_stays_bound_and_assessment_race_rejects(
    workspace_tmp_path, monkeypatch
):
    m = api()
    instance, binding, _, lifecycle = service(workspace_tmp_path, monkeypatch)
    result = instance.execute(
        m.LiteratureRequest(operation="fetch", provider="pubmed", identifier="123"),
        request_id="one",
        context_id="ctx",
        binding=binding,
    )
    revised = binding.model_copy(update={"review_binding_sha256": digest("new review")})
    instance.bind_claim(revised)
    assert assess(instance, revised, result["snapshot_id"])["eligible"]
    from devbench.analysis import method_sources

    monkeypatch.setattr(
        method_sources,
        "_fetch_hop",
        lambda *a, **k: (
            None,
            PUBMED.replace(b"reduced", b"increased"),
            "application/xml",
        ),
    )
    instance.execute(
        m.LiteratureRequest(operation="fetch", provider="pubmed", identifier="123"),
        request_id="two",
        context_id="ctx",
        binding=binding,
    )
    assert not m.literature_use_current(
        lifecycle, "claim", revised.review_binding_sha256
    )

    # Concurrent new counterevidence cannot be overwritten by an older assessment.
    newest = instance.execute(
        m.LiteratureRequest(operation="fetch", provider="pubmed", identifier="123"),
        request_id="three",
        context_id="ctx",
        binding=binding,
    )
    original = instance._write

    def interleave(action, build):
        if action == "assess_claim":
            monkeypatch.setattr(instance, "_write", original)
            monkeypatch.setattr(
                method_sources,
                "_fetch_hop",
                lambda *a, **k: (
                    None,
                    PUBMED.replace(b">123<", b">456<"),
                    "application/xml",
                ),
            )
            instance.execute(
                m.LiteratureRequest(
                    operation="fetch", provider="pubmed", identifier="456"
                ),
                request_id="counterevidence",
                context_id="ctx",
                binding=binding,
            )
        return original(action, build)

    monkeypatch.setattr(instance, "_write", interleave)
    with pytest.raises(Exception, match="evidence changed"):
        assess(instance, binding, newest["snapshot_id"])


def test_explicit_retraction_notice_invalidates_target_not_notice(
    workspace_tmp_path, monkeypatch
):
    m = api()
    instance, binding, _, lifecycle = service(workspace_tmp_path, monkeypatch)
    original = instance.execute(
        m.LiteratureRequest(operation="fetch", provider="pubmed", identifier="123"),
        request_id="original",
        context_id="ctx",
        binding=binding,
    )
    assert assess(instance, binding, original["snapshot_id"])["eligible"]
    notice = PUBMED.replace(b">123<", b">999<").replace(
        b"</MedlineCitation>",
        b'<CommentsCorrectionsList><CommentsCorrections RefType="RetractionOf">'
        b"<PMID>123</PMID><RefSource>Exact notice source</RefSource>"
        b"</CommentsCorrections></CommentsCorrectionsList></MedlineCitation>",
    )
    from devbench.analysis import method_sources

    monkeypatch.setattr(
        method_sources, "_fetch_hop", lambda *a, **k: (None, notice, "application/xml")
    )
    result = instance.execute(
        m.LiteratureRequest(operation="fetch", provider="pubmed", identifier="999"),
        request_id="notice",
        context_id="ctx",
        binding=binding,
    )
    assert result["extraction"]["status"]["retraction"] == "unknown"
    assert instance.state()["retracted"] == {"pmid:123": True}
    assert not m.literature_use_current(
        lifecycle, "claim", binding.review_binding_sha256
    )


@pytest.mark.parametrize("change", ["cancel", "binding", "before_delivery"])
def test_retrieval_acceptance_and_disclosure_are_fenced_but_replay_survives(
    workspace_tmp_path, monkeypatch, change
):
    m = api()
    instance, binding, _, _ = service(workspace_tmp_path, monkeypatch)
    from devbench.analysis import method_sources
    from tests.test_investigation_publication_epochs import authorize
    from tests.test_scientific_authority_migration import _binding

    authority = instance.authority
    with instance.ledger._database() as db:
        revision = authority._revision(db, "study")
    authority.commit(
        "study",
        expected_revision=revision,
        record={
            "kind": "grant",
            "record_id": "grant",
            "binding_id": "binding-a",
            "grant_sha256": "a" * 64,
        },
    )
    authority.activate_execution(
        "study",
        expected_revision=revision + 1,
        binding_id="binding-a",
        grant_id="grant",
        max_jobs=2,
        authorize=authorize,
    )

    def revoke():
        if change in {"cancel", "before_delivery"}:
            authority.cancel_execution("study", actor="operator")
        else:
            with instance.ledger._database() as db:
                rev = authority._revision(db, "study")
            authority.commit(
                "study",
                expected_revision=rev,
                record={**_binding(), "record_id": "binding-b"},
            )

    def hop(*args, **kwargs):
        if change != "before_delivery":
            revoke()
        return None, PUBMED, "application/xml"

    monkeypatch.setattr(method_sources, "_fetch_hop", hop)
    original = instance._deliver
    if change == "before_delivery":

        def deliver(*args):
            revoke()
            return original(*args)

        monkeypatch.setattr(instance, "_deliver", deliver)
    with pytest.raises(Exception, match="fence"):
        instance.execute(
            m.LiteratureRequest(operation="fetch", provider="pubmed", identifier="123"),
            request_id="one",
            context_id="ctx",
            binding=binding,
        )
    state = instance.state()
    snapshot_id = next(iter(state["snapshots"]))
    operation = state["operations"]["one"]
    assert (
        operation["status"] == "blocked"
        and operation["result"]["status"] == "unavailable"
    )
    monkeypatch.setattr(
        method_sources,
        "_fetch_hop",
        lambda *a, **k: pytest.fail("Frozen replay fetched the network"),
    )
    assert (
        instance.replay(snapshot_id)["responses"][0]["content_sha256"]
        == hashlib.sha256(PUBMED).hexdigest()
    )
    with pytest.raises(ValueError, match="stale"):
        instance.replay(snapshot_id, require_current=True)
    assert state["accounting"]["requests"] == 1 and state["accounting"][
        "bytes_observed"
    ] == len(PUBMED)


def test_partial_retry_capture_is_retained_and_charged(workspace_tmp_path, monkeypatch):
    m = api()
    instance, binding, _, _ = service(
        workspace_tmp_path,
        monkeypatch,
        requests=5,
        retries=1,
    )
    xml, metadata = pmc_fixture()
    from devbench.analysis import method_sources

    calls = []

    def hop(host, target, *args, **kwargs):
        calls.append(target)
        if len(calls) == 2:
            raise TimeoutError("First XML failed after metadata capture")
        return (
            None,
            metadata if target.startswith("/metadata/") else xml,
            "application/json"
            if target.startswith("/metadata/")
            else "application/xml",
        )

    monkeypatch.setattr(method_sources, "_fetch_hop", hop)
    result = instance.execute(
        m.LiteratureRequest(
            operation="fetch", provider="pmc", identifier="PMC123", version=1
        ),
        request_id="one",
        context_id="ctx",
        binding=binding,
    )
    assert result["status"] == "available" and len(calls) == 4
    snapshot = instance.replay(result["snapshot_id"])
    assert len(snapshot["responses"]) == 3 and len(snapshot["decode_responses"]) == 2
    assert instance.state()["accounting"]["requests"] == 4
    assert instance.state()["accounting"]["unknown_transfers"] == 1


@pytest.mark.parametrize(
    "result",
    [
        {"count": "0", "idlist": []},
        {"count": "100", "idlist": ["123"]},
    ],
)
def test_search_records_zero_hits_and_unqueried_remainder_distinctly(
    workspace_tmp_path, monkeypatch, result
):
    m = api()
    instance, binding, _, _ = service(workspace_tmp_path, monkeypatch)
    from devbench.analysis import method_sources

    raw = json.dumps({"esearchresult": result}).encode()
    monkeypatch.setattr(
        method_sources, "_fetch_hop", lambda *a, **k: (None, raw, "application/json")
    )
    view = instance.execute(
        m.LiteratureRequest(
            operation="search", provider="pubmed", query="synthetic query", limit=1
        ),
        request_id="search",
        context_id="ctx",
        binding=binding,
    )
    assert view["status"] == ("available" if result["count"] == "0" else "truncated")
    assert view["access"] == "metadata" and view["extraction"] is None
    assert (
        Path(instance.replay(view["snapshot_id"])["responses"][0]["path"]).read_bytes()
        == raw
    )


def test_hostile_redirect_and_unadmitted_action_never_gain_transport_authority(
    workspace_tmp_path, monkeypatch
):
    m = api()
    instance, binding, _, _ = service(workspace_tmp_path, monkeypatch, retries=0)
    from devbench.analysis import method_sources

    calls = []

    def hop(host, target, *args, **kwargs):
        calls.append(host)
        return "https://127.0.0.1/private", None, None

    monkeypatch.setattr(method_sources, "_fetch_hop", hop)
    result = instance.execute(
        m.LiteratureRequest(operation="fetch", provider="pubmed", identifier="123"),
        request_id="one",
        context_id="ctx",
        binding=binding,
    )
    assert result["status"] == "unavailable" and calls == ["eutils.ncbi.nlm.nih.gov"]
    with pytest.raises(ValueError):
        m.LiteratureRequest(operation="shell", provider="pubmed", query="curl private")
    with pytest.raises(ValueError):
        m.LiteratureRequest(
            operation="fetch",
            provider="pubmed",
            identifier="123",
            url="https://other.test",
        )


def test_same_failure_snapshot_can_be_delivered_to_distinct_claim_bindings(
    workspace_tmp_path, monkeypatch
):
    m = api()
    instance, binding, _, lifecycle = service(
        workspace_tmp_path,
        monkeypatch,
        retries=0,
    )
    from devbench.analysis import method_sources
    from devbench.analysis.scientific_review_holds import ReviewEntity

    lifecycle.register_entities((ReviewEntity(entity_id="claim-two", kind="claim"),))
    second = binding.model_copy(
        update={"claim_id": "claim-two", "claim_sha256": digest("second")}
    )
    instance.bind_claim(second)
    monkeypatch.setattr(
        method_sources,
        "_fetch_hop",
        lambda *a, **k: (_ for _ in ()).throw(TimeoutError("same failure")),
    )
    request = m.LiteratureRequest(
        operation="fetch", provider="pubmed", identifier="123"
    )
    first = instance.execute(
        request, request_id="one", context_id="ctx", binding=binding
    )
    other = instance.execute(
        request, request_id="two", context_id="ctx", binding=second
    )
    assert first["snapshot_id"] == other["snapshot_id"]
    assert (
        other["affected_claim_ids"] == ["claim-two"]
        and other["status"] == "unavailable"
    )


def test_limits_cannot_admit_unextractable_passage_length():
    with pytest.raises(ValueError):
        api().LiteratureLimits(max_passage_chars=8001)


def test_cancelled_pmc_metadata_cannot_mutate_current_retraction_status(
    workspace_tmp_path, monkeypatch
):
    m = api()
    instance, binding, _, _ = service(workspace_tmp_path, monkeypatch)
    from devbench.analysis import method_sources
    from tests.test_investigation_publication_epochs import authorize

    with instance.ledger._database() as db:
        revision = instance.authority._revision(db, "study")
    instance.authority.commit(
        "study",
        expected_revision=revision,
        record={
            "kind": "grant",
            "record_id": "grant",
            "binding_id": "binding-a",
            "grant_sha256": "a" * 64,
        },
    )
    instance.authority.activate_execution(
        "study",
        expected_revision=revision + 1,
        binding_id="binding-a",
        grant_id="grant",
        max_jobs=2,
        authorize=authorize,
    )
    _, raw = pmc_fixture()
    value = json.loads(raw)
    value["is_retracted"] = "yes"
    raw = json.dumps(value).encode()
    calls = []

    def hop(host, target, *args, **kwargs):
        calls.append(target)
        instance.authority.cancel_execution("study", actor="operator")
        return None, raw, "application/json"

    monkeypatch.setattr(method_sources, "_fetch_hop", hop)
    with pytest.raises(Exception, match="fence"):
        instance.execute(
            m.LiteratureRequest(
                operation="fetch", provider="pmc", identifier="PMC123", version=1
            ),
            request_id="one",
            context_id="ctx",
            binding=binding,
        )
    assert len(calls) == 1 and not instance.state()["retracted"]
    snapshot = next(iter(instance.state()["snapshots"]))
    assert Path(instance.replay(snapshot)["responses"][0]["path"]).read_bytes() == raw


def test_current_pmc_boolean_s3_and_binary_mime_representation_keeps_raw_bytes(
    workspace_tmp_path, monkeypatch
):
    m = api()
    instance, binding, _, _ = service(workspace_tmp_path, monkeypatch)
    xml, raw = pmc_fixture()
    metadata = json.loads(raw)
    metadata["mid"] = None
    for key in (
        "is_pmc_openaccess",
        "is_manuscript",
        "is_historical_ocr",
        "is_retracted",
    ):
        metadata[key] = metadata[key] == "yes"
    metadata["xml_url"] = metadata["xml_url"].replace(
        "https://pmc-oa-opendata.s3.amazonaws.com/", "s3://pmc-oa-opendata/"
    )
    raw = json.dumps(metadata).encode()
    from devbench.analysis import method_sources

    def hop(host, target, deadline, maximum, *, content_types):
        assert "binary/octet-stream" in content_types
        return (
            None,
            raw if target.startswith("/metadata/") else xml,
            "binary/octet-stream",
        )

    monkeypatch.setattr(method_sources, "_fetch_hop", hop)
    result = instance.execute(
        m.LiteratureRequest(
            operation="fetch", provider="pmc", identifier="PMC123", version=1
        ),
        request_id="one",
        context_id="ctx",
        binding=binding,
    )
    assert result["status"] == "available" and result["access"] == "full_text"
    assert result["metadata"]["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert (
        result["metadata"]["representation_mapping"]["version"]
        == "pmc-boolean-s3-links/1"
    )
    assert (
        Path(
            instance.replay(result["snapshot_id"])["responses"][0]["path"]
        ).read_bytes()
        == raw
    )


def test_private_transport_error_detail_stays_in_local_custody(
    workspace_tmp_path,
    monkeypatch,
):
    m = api()
    instance, binding, _, _ = service(workspace_tmp_path, monkeypatch)
    monkeypatch.setattr(
        m.LiteratureCapability,
        "verify",
        lambda self: (_ for _ in ()).throw(
            PermissionError("synthetic-private-path/detail")
        ),
    )
    result = instance.execute(
        m.LiteratureRequest(operation="fetch", provider="pubmed", identifier="123"),
        request_id="one",
        context_id="ctx",
        binding=binding,
    )
    assert result[
        "failure"
    ] == "PermissionError" and "synthetic-private-path" not in json.dumps(result)
    assert (
        instance.replay(result["snapshot_id"])["failure_detail"]
        == "synthetic-private-path/detail"
    )


def test_pmc_self_uri_is_inert_metadata_not_a_second_fetch(
    workspace_tmp_path,
    monkeypatch,
):
    m = api()
    instance, binding, _, _ = service(workspace_tmp_path, monkeypatch)
    xml, _ = pmc_fixture()
    xml = xml.replace(
        b"</article-meta>",
        b'<self-uri xmlns:xlink="http://www.w3.org/1999/xlink" content-type="pdf" '
        b'xlink:href="uninspected.pdf"/></article-meta>',
    )
    _, raw = pmc_fixture()
    metadata = json.loads(raw)
    metadata["xml_url"] = (
        metadata["xml_url"].split("?")[0] + "?md5=" + hashlib.md5(xml).hexdigest()
    )
    from devbench.analysis import method_sources

    calls = []

    def hop(host, target, *args, **kwargs):
        calls.append(target)
        return (
            None,
            json.dumps(metadata).encode() if target.startswith("/metadata/") else xml,
            "application/json"
            if target.startswith("/metadata/")
            else "application/xml",
        )

    monkeypatch.setattr(method_sources, "_fetch_hop", hop)
    result = instance.execute(
        m.LiteratureRequest(
            operation="fetch", provider="pmc", identifier="PMC123", version=1
        ),
        request_id="one",
        context_id="ctx",
        binding=binding,
    )
    assert (
        result["status"] == "available"
        and len(calls) == 2
        and all("pdf" not in item for item in calls)
    )


def test_new_review_binding_cannot_silently_drop_existing_literature_dependency(
    workspace_tmp_path, monkeypatch
):
    m = api()
    instance, binding, _, lifecycle = service(workspace_tmp_path, monkeypatch)
    result = instance.execute(
        m.LiteratureRequest(operation="fetch", provider="pubmed", identifier="123"),
        request_id="one",
        context_id="ctx",
        binding=binding,
    )
    assert assess(instance, binding, result["snapshot_id"])["eligible"]
    assert not m.literature_use_current(
        lifecycle, "claim", digest("different review without Literature binding")
    )
    assert not m.literature_use_current(
        lifecycle, None, digest("different review without Literature binding")
    )
