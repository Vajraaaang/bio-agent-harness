"""Synthetic entitlement and cumulative-join controls."""
import hashlib

import pytest

from devbench.engine.workflow_operations import WorkflowOperationLedger
from tests.test_scientific_authority_migration import _binding

def setup(tmp_path, *, allow_outcomes=True, allow_statistics=True, role="controller", phase="analysis"):
    from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker

    ledger = WorkflowOperationLedger(tmp_path / "operations.sqlite", scientific=True)
    ledger.scientific.commit("study", expected_revision=0, record=_binding())
    broker = EvidenceBroker(ledger.scientific)
    policy = AccessPolicy("policy", 1, allow_outcomes=allow_outcomes, allow_statistics=allow_statistics,
                          diagnostic_policy_id="d" * 64)
    broker.register_policy(policy)
    broker.register_context("ctx", principal_id="scientist", lineage_id="study", role=role, phase=phase,
                            policy_sha256=policy.sha256())
    return ledger, broker, policy


def source(broker, name="result", *, tags=(), classification="target_outcome", parents=()):
    return broker.register_source(name, content_sha256=("b" * 64 if name == "result" else hashlib.sha256(name.encode()).hexdigest()),
                                  roots=("a" * 64,), units=("donor",),
                                  populations=("population",), tags=tags, classification=classification, parents=parents)


@pytest.mark.wal
def test_phase_and_hidden_statistic_operations_are_enforced(tmp_path):
    from devbench.analysis.evidence_access import AccessDenied

    _, broker, _ = setup(tmp_path, allow_statistics=False, role="challenger", phase="design_initial")
    source(broker, tags=("controller_narrative",))
    with pytest.raises(AccessDenied):
        broker.delivery("ctx", "result").release({"summary": "controller explanation"})
    source(broker, "plain")
    with pytest.raises(AccessDenied):
        broker.delivery("ctx", "plain").check_query({"sort_by": "q_value"})
    with pytest.raises(AccessDenied):
        broker.delivery("ctx", "plain").check_query({"mode": "aggregate"})


@pytest.mark.wal
def test_renamed_context_and_copied_source_inherit_exposure(tmp_path):
    _, broker, policy = setup(tmp_path)
    source(broker)
    broker.delivery("ctx", "result").release({"effect": 1.0})
    broker.register_context("renamed", principal_id="scientist", lineage_id="study", role="controller",
                            phase="analysis", policy_sha256=policy.sha256())
    source(broker, "copy", classification="structural", parents=("result",))
    assert broker.exposure("renamed", "copy")["classification"] == "target_outcome"


@pytest.mark.wal
def test_training_cannot_receive_protected_evaluation_handles(tmp_path):
    from devbench.analysis.evidence_access import AccessDenied

    _, broker, _ = setup(tmp_path, role="training", phase="training")
    source(broker, tags=("protected_evaluation",))
    with pytest.raises(AccessDenied):
        broker.delivery("ctx", "result").release({"handle": "protected-evaluator"})


@pytest.mark.wal
def test_query_page_is_reserved_and_hidden_filter_is_rejected(tmp_path):
    from devbench.analysis.episode_evidence_queries import query_result
    from devbench.analysis.evidence_access import AccessDenied
    from tests.test_episode_evidence_queries import result

    _, broker, _ = setup(tmp_path)
    source(broker)
    delivery = broker.delivery("ctx", "result")
    view = query_result(result(), {"sort_by": "p_value", "limit": 1}, evidence_delivery=delivery)
    assert view["rows"][0]["gene_id"] == "plantA"
    assert broker.exposure("ctx", "result")["possible_reservations"] == 1
    _, denied, _ = setup(tmp_path / "denied", allow_statistics=False)
    source(denied)
    with pytest.raises(AccessDenied):
        query_result(result(), {"filters": {"q_value_lte": .05}}, evidence_delivery=denied.delivery("ctx", "result"))


@pytest.mark.wal
def test_linked_principal_and_phase_cannot_clear_knowledge(tmp_path):
    _, broker, policy = setup(tmp_path)
    source(broker)
    broker.delivery("ctx", "result").release({"effect": 1})
    broker.register_context("linked", principal_id="new-session", lineage_id="study", role="challenger",
                            phase="publication_initial", policy_sha256=policy.sha256(), parents=("ctx",))
    assert broker.exposure("linked", "result")["classification"] == "target_outcome"


@pytest.mark.wal
def test_policy_update_invalidates_old_context_before_delivery(tmp_path):
    from devbench.analysis.evidence_access import AccessDenied, AccessPolicy

    _, broker, _ = setup(tmp_path)
    source(broker)
    broker.register_policy(AccessPolicy("policy", 2))
    sent = []
    with pytest.raises(AccessDenied, match="stale"):
        broker.delivery("ctx", "result").transmit({"effect": 1}, sent.append)
    assert not sent


@pytest.mark.wal
def test_nested_aliases_and_refined_watermark_are_recorded(tmp_path):
    from devbench.analysis.evidence_access import AccessDenied

    _, broker, _ = setup(tmp_path)
    source(broker)
    broker.reserve("ctx", "result", {"effect": 1})
    row = broker.reserve("ctx", "result", {"effect": 2}, expected_watermark=0)
    assert row["recomputed_from_stale_watermark"]
    _, denied, _ = setup(tmp_path / "denied", allow_statistics=False)
    source(denied)
    with pytest.raises(AccessDenied):
        denied.delivery("ctx", "result").release({"status": {"synthesis": {"PValue": .01}}})


@pytest.mark.wal
def test_identical_content_cannot_drop_source_scope_or_tags(tmp_path):
    from devbench.analysis.evidence_access import AccessDenied
    _, broker, _ = setup(tmp_path, role="training", phase="training")
    source(broker, tags=("full_cohort_learned_state",))
    broker.register_source("renamed", content_sha256="b" * 64, roots=("e" * 64,),
                           classification="structural")
    with pytest.raises(AccessDenied):
        broker.delivery("ctx", "renamed").release({"embedding": [1, 2]})


@pytest.mark.wal
def test_full_cohort_state_in_nested_payload_is_not_training_input(tmp_path):
    from devbench.analysis.evidence_access import AccessDenied
    _, broker, _ = setup(tmp_path, role="training", phase="training")
    source(broker)
    with pytest.raises(AccessDenied):
        broker.delivery("ctx", "result").release({"cache": {"full_cohort_learned_state": [1]}})


@pytest.mark.wal
def test_inherited_protected_exposure_keeps_downstream_restrictions(tmp_path):
    from devbench.analysis.evidence_access import AccessDenied
    _, broker, policy = setup(tmp_path)
    source(broker)
    broker.delivery("ctx", "result").release({"protected_evaluation": {"effect": 1}})
    broker.register_context("training", principal_id="scientist", lineage_id="study", role="training",
                            phase="training", policy_sha256=policy.sha256())
    broker.register_source("summary", content_sha256="c" * 64, roots=("a" * 64,), classification="structural")
    with pytest.raises(AccessDenied):
        broker.delivery("training", "summary").release({"summary": "possible protected ancestry"})


@pytest.mark.wal
def test_policy_binds_allowed_source_and_population_scope(tmp_path):
    from devbench.analysis.evidence_access import AccessDenied, AccessPolicy

    _, broker, _ = setup(tmp_path)
    policy = AccessPolicy("bounded", 1, allowed_roots=("a" * 64,), allowed_units=("donor",),
                          allowed_populations=("population",))
    broker.register_policy(policy)
    broker.register_context("bounded", principal_id="bounded-reviewer", lineage_id="study", role="challenger",
                            phase="design_initial", policy_sha256=policy.sha256())
    source(broker, "allowed", classification="structural")
    assert broker.delivery("bounded", "allowed").release({"donor_count": 2}) == {"donor_count": 2}
    broker.register_source("unspecified", content_sha256="c" * 64, roots=("a" * 64,),
                           units=("donor",), classification="structural")
    broker.register_source("outside", content_sha256="d" * 64, roots=("e" * 64,),
                           units=("donor",), populations=("population",), classification="structural")
    for name in ("unspecified", "outside"):
        with pytest.raises(AccessDenied):
            broker.delivery("bounded", name).release({"donor_count": 2})


@pytest.mark.wal
def test_policy_and_context_writes_cannot_overrun_read_inventories(tmp_path):
    from devbench.analysis.evidence_access import AccessDenied, AccessPolicy

    _, broker, policy = setup(tmp_path)
    source(broker)
    for index in range(255):
        broker.register_policy(AccessPolicy(f"other-{index}", 1))
        broker.register_context(f"context-{index}", principal_id="other", lineage_id="study",
                                role="scientist", phase="readback", policy_sha256=policy.sha256())

    # Exact re-registration is idempotent at capacity.
    broker.register_policy(policy)
    broker.register_context("ctx", principal_id="scientist", lineage_id="study", role="controller",
                            phase="analysis", policy_sha256=policy.sha256())
    with pytest.raises(AccessDenied, match="AccessPolicy inventory"):
        broker.register_policy(AccessPolicy("overflow", 1))
    with pytest.raises(AccessDenied, match="Context inventory"):
        broker.register_context("overflow", principal_id="other", lineage_id="study",
                                role="scientist", phase="readback", policy_sha256=policy.sha256())
    assert broker.delivery("ctx", "result").release({"effect": 1}) == {"effect": 1}


@pytest.mark.wal
def test_reservation_overflow_preserves_existing_exposure_and_watermark(tmp_path):
    from devbench.analysis.evidence_access import AccessDenied
    from devbench.engine.workflow_operations import _digest, _json

    ledger, broker, _ = setup(tmp_path)
    source(broker)
    first = broker.reserve("ctx", "result", {"effect": 1})
    # Populate valid reservations at the boundary without re-reading all previous
    # disclosures for each synthetic row.
    rows = []
    for sequence in range(2, 4096):
        identity = f"seed-{sequence}"
        encoded = _json({**first, "reservation_id": identity, "watermark": sequence})
        rows.append((identity, encoded, _digest(encoded)))
    with ledger._database() as db:
        db.executemany("INSERT INTO scientific_disclosures VALUES (?,?,?)", rows)
        db.execute("UPDATE scientific_disclosure_clock SET watermark=4095 WHERE singleton=1")
    last = broker.reserve("ctx", "result", {"effect": 2})
    assert last["watermark"] == 4096
    with pytest.raises(AccessDenied, match="Exposure history"):
        broker.reserve("ctx", "result", {"effect": 3},
                       _prepare_page=lambda *_: pytest.fail("saturated broker constructed a page"))
    broker.mark_attempted(last["reservation_id"])
    assert broker.exposure("ctx", "result")["possible_reservations"] == 4096
    with ledger._database() as db:
        assert db.execute("SELECT watermark FROM scientific_disclosure_clock").fetchone()[0] == 4096


@pytest.mark.wal
def test_copied_source_write_preserves_alias_and_merged_scope_bounds(tmp_path):
    from devbench.analysis.evidence_access import AccessDenied

    _, broker, _ = setup(tmp_path)
    for index in range(64):
        broker.register_source(f"alias-{index}", content_sha256="b" * 64, roots=("a" * 64,),
                               classification="structural")
    broker.register_source("alias-0", content_sha256="b" * 64, roots=("a" * 64,),
                           classification="structural")
    with pytest.raises(AccessDenied, match="Copied source ancestry"):
        broker.register_source("overflow", content_sha256="b" * 64, roots=("a" * 64,),
                               classification="structural")
    assert broker.delivery("ctx", "alias-0").release({"effect": 1}) == {"effect": 1}

    broker.register_source("many-roots", content_sha256="c" * 64, roots=tuple(f"{i:064x}" for i in range(64)),
                           classification="structural")
    with pytest.raises(AccessDenied, match="Copied source scope"):
        broker.register_source("root-overflow", content_sha256="c" * 64, roots=("e" * 64,),
                               classification="structural")
    assert broker.exposure("ctx", "many-roots")["possible_reservations"] == 0


@pytest.mark.wal
def test_context_registration_checks_transitive_inheritance_bound(tmp_path):
    from devbench.analysis.evidence_access import AccessDenied

    _, broker, policy = setup(tmp_path)
    source(broker)
    parent = "ctx"
    for index in range(127):
        current = f"chain-{index}"
        broker.register_context(current, principal_id="scientist", lineage_id="study", role="controller",
                                phase="analysis", policy_sha256=policy.sha256(), parents=(parent,))
        parent = current
    with pytest.raises(AccessDenied, match="Context inheritance"):
        broker.register_context("overflow", principal_id="scientist", lineage_id="study", role="controller",
                                phase="analysis", policy_sha256=policy.sha256(), parents=(parent,))
    assert broker.exposure(parent, "result")["possible_reservations"] == 0


def test_disclosure_source_walk_deduplicates_peer_ancestry_and_bounds_reads():
    from devbench.core._disclosure_graph import MAX_SOURCE_ANCESTRY, walk_source_ancestry

    visited = []

    def load_source(identity):
        visited.append(identity)
        return {"content_sha256": identity, "parents": [identity]}

    def peer_parents(content_sha256):
        ordinal = int(content_sha256)
        return ["0", str(ordinal + 1)] if ordinal < MAX_SOURCE_ANCESTRY else []

    denied = ValueError("disclosure ancestry exceeds bound")
    plain = dict(walk_source_ancestry(["0", "0"], load_source, limit_error=denied))
    assert list(plain) == visited == ["0"]
    visited.clear()
    with pytest.raises(ValueError, match="disclosure ancestry exceeds bound"):
        list(walk_source_ancestry(["0", "0"], load_source, limit_error=denied, peer_parents=peer_parents))
    assert visited == [str(index) for index in range(MAX_SOURCE_ANCESTRY)]
