"""Synthetic retained evidence and durable query receipts."""

import hashlib
import json

import pytest

from devbench.analysis.episode_evidence_queries import query_result
from tests.test_evidence_access_policy import setup, source


def family(n=46):
    rows = [
        {
            "gene_id": f"g{i:03}",
            "status": "tested",
            "effect": -2 if i == 39 else 1,
            "p": 0.01,
            "q_local": 0.02,
            "q_budget": 0.04,
        }
        for i in range(n)
    ]
    return {
        "format": "devbench.erql-family-synthesis/1",
        "allocation_sha256": "a" * 64,
        "families": [
            {
                "family_id": "family",
                "status": "ready",
                "genes": rows,
                "feature_ids": [row["gene_id"] for row in rows],
                "eligible_features": [row["gene_id"] for row in rows],
            }
        ],
    }


@pytest.mark.wal
def test_authenticated_pages_bind_complete_query_and_disclosed_bytes(tmp_path):
    ledger, broker, _ = setup(tmp_path)
    source(broker)
    data = family()
    delivery = broker.delivery("ctx", "result")
    query = {"target_id": "family", "limit": 16}
    page = query_result(data, query, evidence_delivery=delivery)
    receipt = page["query_receipt"]
    assert (
        receipt["total_matched"],
        receipt["total_returned"],
        receipt["complete"],
    ) == (46, 16, False)
    stored = broker.reservation(receipt["disclosure_reservation_id"])
    encoded = json.dumps(
        page, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    assert stored["content_sha256"] == hashlib.sha256(encoded.encode()).hexdigest()
    assert stored["policy_sha256"] == receipt["policy_sha256"]

    from devbench.analysis.evidence_access import EvidenceBroker
    from devbench.engine.workflow_operations import WorkflowOperationLedger

    reopened = EvidenceBroker(WorkflowOperationLedger(ledger.path).scientific).delivery(
        "ctx", "result"
    )
    second = query_result(
        data, {**query, "cursor": receipt["next_cursor"]}, evidence_delivery=reopened
    )
    third = query_result(
        data,
        {**query, "cursor": second["query_receipt"]["next_cursor"]},
        evidence_delivery=reopened,
    )
    assert third["query_receipt"]["complete"]
    assert (
        next(row for row in third["rows"] if row["gene_id"] == "g039")["effect"] == -2
    )


@pytest.mark.wal
def test_cursor_rejects_changed_request_artifact_and_principal(tmp_path):
    from devbench.analysis.evidence_access import AccessDenied

    _, broker, policy = setup(tmp_path)
    source(broker)
    data = family()
    delivery = broker.delivery("ctx", "result")
    query = {"target_id": "family", "limit": 2}
    cursor = query_result(data, query, evidence_delivery=delivery)["query_receipt"][
        "next_cursor"
    ]
    for changed in (
        {"cursor": "0" * 32},
        {"offset": 2},
        {"filters": {"gene_id": "g001"}},
    ):
        with pytest.raises((AccessDenied, ValueError)):
            query_result(
                data, {**query, "cursor": cursor, **changed}, evidence_delivery=delivery
            )
    changed_data = family()
    changed_data["families"][0]["genes"][3]["effect"] = 3
    with pytest.raises(AccessDenied):
        query_result(
            changed_data, {**query, "cursor": cursor}, evidence_delivery=delivery
        )
    broker.register_context(
        "other",
        principal_id="other",
        lineage_id="study",
        role="controller",
        phase="analysis",
        policy_sha256=policy.sha256(),
    )
    with pytest.raises(AccessDenied):
        query_result(
            data,
            {**query, "cursor": cursor},
            evidence_delivery=broker.delivery("other", "result"),
        )


@pytest.mark.wal
def test_policy_change_and_unadmitted_significance_reject_continuation(tmp_path):
    from devbench.analysis.evidence_access import AccessDenied, AccessPolicy
    from tests.test_episode_evidence_queries import result

    _, broker, _ = setup(tmp_path)
    source(broker)
    delivery = broker.delivery("ctx", "result")
    query = {"target_id": "family", "limit": 3}
    data = family()
    page = query_result(data, query, evidence_delivery=delivery)
    assert broker.verify_query_page(page)
    changed_page = {
        **page,
        "query_receipt": {**page["query_receipt"], "total_matched": 99},
    }
    assert not broker.verify_query_page(changed_page)
    for field in ("q_value", "q_budget"):
        with pytest.raises(AccessDenied):
            query_result(result(), {"sort_by": field}, evidence_delivery=delivery)

    policy = AccessPolicy("policy", 2, allow_outcomes=True, allow_statistics=False)
    broker.register_policy(policy)
    broker.register_context(
        "limited",
        principal_id="scientist",
        lineage_id="study",
        role="controller",
        phase="analysis",
        policy_sha256=policy.sha256(),
        parents=("ctx",),
    )
    limited = broker.delivery("limited", "result")
    for forbidden in (
        {"sort_by": "q_budget"},
        {"filters": {"p_value_lte": 0.01}},
        {"mode": "aggregate"},
        {"cursor": page["query_receipt"]["next_cursor"]},
    ):
        with pytest.raises(AccessDenied):
            query_result(data, {**query, **forbidden}, evidence_delivery=limited)
    structural = query_result(data, query, evidence_delivery=limited)
    assert "effect" in structural["rows"][0] and "q_budget" not in structural["rows"][0]


@pytest.mark.wal
def test_page_byte_budget_advances_by_returned_rows_and_rejects_exhaustion(
    tmp_path, monkeypatch
):
    from devbench.analysis import episode_evidence_queries as engine
    from devbench.analysis.evidence_access import AccessDenied

    _, broker, _ = setup(tmp_path)
    source(broker)
    delivery = broker.delivery("ctx", "result")
    data = family(21)
    query = {"target_id": "family", "limit": 20}
    monkeypatch.setattr(engine, "MAX_VIEW_BYTES", 2600)
    ids, cursor = [], None
    while True:
        page = query_result(
            data,
            query if cursor is None else {**query, "cursor": cursor},
            evidence_delivery=delivery,
        )
        receipt = page["query_receipt"]
        assert receipt["offset"] == len(ids)
        assert receipt["total_returned"] == len(page["rows"])
        assert len(json.dumps(page).encode()) <= 2600
        ids.extend(row["gene_id"] for row in page["rows"])
        cursor = receipt["next_cursor"]
        if receipt["complete"]:
            break
    assert ids == [f"g{i:03}" for i in range(21)]
    with pytest.raises(AccessDenied):
        query_result(
            data,
            {**query, "cursor": receipt["disclosure_reservation_id"]},
            evidence_delivery=delivery,
        )
    before = broker.exposure("ctx", "result")["possible_reservations"]
    monkeypatch.setattr(engine, "MAX_VIEW_BYTES", 400)
    with pytest.raises(ValueError, match="budget"):
        query_result(data, query, evidence_delivery=delivery)
    assert broker.exposure("ctx", "result")["possible_reservations"] == before


def test_effect_ties_use_feature_identity_after_reordering():
    data = family()
    query = {
        "target_id": "family",
        "sort_by": "effect",
        "descending": True,
        "limit": 10,
    }
    first = query_result(data, query)["rows"]
    data["families"][0]["genes"].reverse()
    assert query_result(data, query)["rows"] == first
    assert [row["gene_id"] for row in first] == [f"g{i:03}" for i in range(10)]


@pytest.mark.wal
def test_corrupt_companion_page_fails_before_reservation(tmp_path):
    from pathlib import Path

    from tests.test_evidence_sparse_queries import profiles

    _, broker, _ = setup(tmp_path / "authority")
    source(broker)
    bindings = profiles(tmp_path)
    delivery = broker.delivery("ctx", "result")
    query = {"mode": "counts", "limit": 1}
    page = query_result({}, query, bindings=bindings, evidence_delivery=delivery)
    receipt = page["query_receipt"]
    assert receipt["missing_count"] == 2 and receipt["total_returned"] == 1
    continuation = {**query, "cursor": receipt["next_cursor"]}
    next_page = query_result(
        {}, continuation, bindings=bindings, evidence_delivery=delivery
    )
    assert next_page["query_receipt"]["complete"]
    path = Path(bindings["counts_path"])
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="hash mismatch"):
        query_result({}, continuation, bindings=bindings, evidence_delivery=delivery)
    assert broker.exposure("ctx", "result")["possible_reservations"] == 2


@pytest.mark.wal
def test_episode_continuation_is_reserved_and_historical_replay_verifies(
    workspace_tmp_path,
):
    from devbench.analysis.episode_agent import Selection, drive_episode
    from tests.test_analysis_episodes import create
    from tests.test_episode_agent import sequence, verify
    from tests.test_episode_analyst_runtime import AnalystSelector, finish

    root = workspace_tmp_path
    _, broker, _ = setup(root / "authority")
    source(broker)
    episode, _ = create(root)
    query = {"kind": "query_evidence", "source_action": 4, "query": {"limit": 2}}

    class ContinuedSelector(AnalystSelector):
        def select(self, context, output, **kwargs):
            choice = super().select(context, output, **kwargs)
            if len(context["history"]) != 5:
                return choice
            page = context["history"][-1]["observation"]["query_result"]
            assert broker.verify_query_page(page)
            action = {
                **query,
                "query": {
                    **query["query"],
                    "cursor": page["query_receipt"]["next_cursor"],
                },
            }
            return Selection(action, choice.evidence, choice.decision_record)

    selector = ContinuedSelector(sequence()[:-1] + [query, query, finish()])
    final = drive_episode(
        episode,
        selector,
        analyst_profile="analyst_v1",
        allow_data_execution=True,
        evidence_delivery=broker.delivery("ctx", "result"),
    )
    assert final["outcome"] == "completed"
    verify(episode)
    second = selector.contexts[-1]["history"][-1]["observation"]["query_result"]
    assert second["query_receipt"]["offset"] == 2 and broker.verify_query_page(second)
    replay = episode.replay(
        root / "replay",
        expected_root=episode.receipt["root_hash"],
        allow_data_execution=True,
    )
    assert replay.outcome == "completed"
    verify(replay)


@pytest.mark.wal
def test_withheld_family_distinguishes_missing_rows_from_zero_matches(tmp_path):
    _, broker, _ = setup(tmp_path)
    source(broker)
    data = family(3)
    data["families"][0].update(status="withheld", genes=[])
    delivery = broker.delivery("ctx", "result")
    page = query_result(data, {}, evidence_delivery=delivery)
    assert (
        page["query_receipt"]["total_matched"]
        == page["query_receipt"]["missing_count"]
        == 3
    )
    assert all(
        row["status"] == "unavailable" and "q_budget" not in row for row in page["rows"]
    )
    empty = query_result(data, {"gene_ids": ["absent"]}, evidence_delivery=delivery)
    assert (
        empty["query_receipt"]["total_matched"]
        == empty["query_receipt"]["missing_count"]
        == 0
    )
    assert empty["query_receipt"]["complete"]


def tier_a_parquet(workspace, broker, *, adjusted=(), replace=None, row_count=46):
    from collections import Counter

    import pyarrow as pa
    import pyarrow.parquet as pq

    from devbench.core.protected_partitions import PartitionVault
    from devbench.platform.adaptive_workflow import ArtifactVersion

    records = [
        {
            "gene_id": f"g{index:03}",
            "hypothesis_id": f"g{index:03}",
            "estimate_status": "failed" if index == 39 else "estimated",
            "coordinate_ids": [] if index == 39 else ["estimate"],
            "values": [] if index == 39 else [2.0],
            "effect": None if index == 39 else 2.0,
            "intervals": []
            if index == 39
            else [
                {"lower": 1.5, "upper": 2.5, "confidence_level": 0.95},
            ],
            "p_value": None if index == 39 else 0.01,
            "q_value": None if index == 39 else 0.05,
            "missing_reason": "non_estimable" if index == 39 else None,
            **{
                name: None if index == 39 else 0.05 * (offset + 1)
                for offset, name in enumerate(adjusted)
            },
        }
        for index in reversed(range(row_count))
    ]
    if replace is not None:
        records[0].update(replace)
    summary = {
        "format": "devbench.tier-a-result/1",
        "target_id": "mean_contrast",
        "family_id": "plan_family",
        "row_count": len(records),
        "tested_count": row_count - 1,
        "status_counts": dict(Counter(row["estimate_status"] for row in records)),
        "request_sha256": "a" * 64,
        "family_sha256": "b" * 64,
        "measurement": "continuous",
        "biological_unit": "sample",
        "claim_scope": "admitted_research_inference",
    }
    interval = pa.struct(
        [(name, pa.float64()) for name in ("lower", "upper", "confidence_level")]
    )
    schema = pa.schema(
        [
            ("gene_id", pa.string()),
            ("hypothesis_id", pa.string()),
            ("estimate_status", pa.string()),
            ("coordinate_ids", pa.list_(pa.string())),
            ("values", pa.list_(pa.float64())),
            ("effect", pa.float64()),
            ("intervals", pa.list_(interval)),
            ("p_value", pa.float64()),
            ("q_value", pa.float64()),
            ("missing_reason", pa.string()),
            *((name, pa.float64()) for name in adjusted),
        ],
        metadata={b"devbench.result": json.dumps(summary).encode()},
    )
    stream = pa.BufferOutputStream()
    table = pa.Table.from_pylist(records, schema=schema)
    pq.write_table(table, stream, row_group_size=12 if row_count == 46 else 8192)
    vault = PartitionVault(workspace / "tier-a-output")
    identity = vault.put(stream.getvalue().to_pybytes())
    artifact = ArtifactVersion(
        version_id="tier-a-synthetic",
        sha256=identity,
        kind="table",
        representation="parquet",
        biological_unit="sample",
        unit_membership_sha256="e" * 64,
        permitted_uses=("checked_observation", "admitted_research_inference"),
    )
    broker.register_source(
        "tier-a",
        content_sha256=identity,
        roots=("f" * 64,),
        classification="target_outcome",
    )
    return artifact, lambda value: vault.open(value.sha256)


@pytest.mark.wal
def test_parquet_query_reads_full_transcriptome_with_bounded_page(workspace_tmp_path):
    from devbench.analysis.episode_evidence_queries import query_parquet_result

    root = workspace_tmp_path
    _, broker, _ = setup(root / "authority")
    artifact, open_artifact = tier_a_parquet(root, broker, row_count=100_000)
    page = query_parquet_result(
        artifact,
        {"limit": 2, "sort_by": "effect", "descending": True},
        open_artifact=open_artifact,
        evidence_delivery=broker.delivery("ctx", "tier-a"),
    )
    assert [row["gene_id"] for row in page["rows"]] == ["g000", "g001"]
    assert page["query_receipt"]["total_matched"] == 100_000
    assert page["query_receipt"]["failed_count"] == 1
    assert broker.verify_query_page(page)


def test_partition_vault_streams_large_files_and_deduplicates(workspace_tmp_path):
    import hashlib
    from concurrent.futures import ThreadPoolExecutor

    from devbench.core.protected_partitions import PartitionVault

    source = workspace_tmp_path / "payload"
    size = 16_000_001
    with source.open("wb") as stream:
        stream.truncate(size)
    with source.open("rb") as stream:
        expected = hashlib.file_digest(stream, "sha256").hexdigest()
    default_vault = PartitionVault(workspace_tmp_path / "legacy")
    with pytest.raises(ValueError, match="byte bound"):
        default_vault.put_file(source)
    assert list(default_vault.root.iterdir()) == []
    vault = PartitionVault(workspace_tmp_path / "large", max_bytes=size)
    with ThreadPoolExecutor(2) as workers:
        assert list(workers.map(vault.put_file, [source, source])) == [
            expected,
            expected,
        ]
    retained = vault.root / expected
    assert list(vault.root.iterdir()) == [retained]
    assert retained.stat().st_ino != source.stat().st_ino
    assert retained.stat().st_nlink == 1
    with vault.open(expected) as stream:
        assert hashlib.file_digest(stream, "sha256").hexdigest() == expected
    with pytest.raises(ValueError, match="byte budget"):
        with PartitionVault(vault.root).open(expected):
            pytest.fail("the default vault accepted an oversized retained file")


def test_partition_vault_rejects_linked_staging_sources(workspace_tmp_path):
    from devbench.core.protected_partitions import PartitionVault

    vault = PartitionVault(workspace_tmp_path / "vault")
    source = workspace_tmp_path / "payload"
    source.write_bytes(b"payload")
    symlink = workspace_tmp_path / "symlink"
    symlink.symlink_to(source)
    hardlink = workspace_tmp_path / "hardlink"
    hardlink.hardlink_to(source)
    for path in (source, symlink, hardlink):
        with pytest.raises(ValueError):
            vault.put_file(path)
    assert list(vault.root.iterdir()) == []


def test_partition_vault_rejects_replaced_source_before_publication(
    workspace_tmp_path,
    monkeypatch,
):
    import os

    from devbench.core.protected_partitions import PartitionVault

    vault = PartitionVault(workspace_tmp_path / "vault")
    source = workspace_tmp_path / "payload"
    source.write_bytes(b"payload")
    old_inode = source.stat().st_ino
    original_read = os.read
    changed = False

    def replace_after_read(fd, size):
        nonlocal changed
        data = original_read(fd, size)
        if not changed and os.fstat(fd).st_ino == old_inode:
            source.replace(workspace_tmp_path / "original")
            source.write_bytes(data)
            changed = True
        return data

    monkeypatch.setattr(os, "read", replace_after_read)
    with pytest.raises(ValueError, match="source changed"):
        vault.put_file(source)
    assert changed and list(vault.root.iterdir()) == []


@pytest.mark.wal
def test_parquet_summary_and_opaque_pages_use_exact_result(workspace_tmp_path):
    from devbench.analysis.episode_evidence_queries import (
        parquet_evidence_summary,
        query_parquet_result,
    )

    root = workspace_tmp_path
    _, broker, _ = setup(root / "authority")
    artifact, open_artifact = tier_a_parquet(root, broker)
    exploratory = artifact.model_copy(update={"permitted_uses": ("exploratory",)})
    with pytest.raises(PermissionError, match="admitted Tier A"):
        parquet_evidence_summary(exploratory, open_artifact=open_artifact)
    delivery = broker.delivery("ctx", "tier-a")
    kwargs = {"open_artifact": open_artifact, "evidence_delivery": delivery}
    summary = parquet_evidence_summary(artifact, open_artifact=open_artifact)
    assert summary["row_count"] == 46
    assert summary["tested_count"] == 45
    assert "rows" not in summary and "p_value" not in summary

    whole = query_parquet_result(artifact, {"mode": "family_summary"}, **kwargs)
    assert whole["family_summary"] == summary
    assert broker.verify_query_page(whole)
    query = {"limit": 20, "sort_by": "effect", "descending": True}
    ids, continuation = [], {}
    for offset in (0, 20, 40):
        page = query_parquet_result(artifact, {**query, **continuation}, **kwargs)
        receipt = page["query_receipt"]
        assert receipt["source_sha256"] == artifact.sha256
        assert receipt["registered_source_sha256"] == artifact.sha256
        assert receipt["offset"] == offset and receipt["failed_count"] == 1
        assert receipt["total_matched"] == 46
        assert broker.verify_query_page(page)
        ids.extend(row["gene_id"] for row in page["rows"])
        continuation = {"cursor": receipt["next_cursor"]}
    assert receipt["complete"] and ids.pop() == "g039"
    assert ids == [f"g{index:03}" for index in range(46) if index != 39]


@pytest.mark.parametrize("adjusted", [(), ("q_local", "q_budget")])
@pytest.mark.wal
def test_parquet_rows_disclose_only_retained_statistics(workspace_tmp_path, adjusted):
    from devbench.analysis.episode_evidence_queries import query_parquet_result

    root = workspace_tmp_path
    _, broker, _ = setup(root / "authority")
    artifact, open_artifact = tier_a_parquet(root, broker, adjusted=adjusted)
    page = query_parquet_result(
        artifact,
        {"gene_ids": ["g000", "g039"]},
        open_artifact=open_artifact,
        evidence_delivery=broker.delivery("ctx", "tier-a"),
    )

    estimated, failed = page["rows"]
    assert (estimated["effect"], estimated["p_value"], estimated["q_value"]) == (
        2.0,
        0.01,
        0.05,
    )
    assert estimated["intervals"] == [
        {"lower": 1.5, "upper": 2.5, "confidence_level": 0.95},
    ]
    assert (failed["effect"], failed["p_value"], failed["q_value"]) == (
        None,
        None,
        None,
    )
    assert failed["intervals"] == []
    if adjusted:
        assert (estimated["q_local"], estimated["q_budget"]) == (0.05, 0.1)
        assert (failed["q_local"], failed["q_budget"]) == (None, None)
    else:
        assert {"q_local", "q_budget"}.isdisjoint(estimated)
        assert {"q_local", "q_budget"}.isdisjoint(failed)
    assert page["query_receipt"]["statistics_included"] is True
    assert broker.verify_query_page(page)
    estimated["q_value"] = 0.04
    assert not broker.verify_query_page(page)


@pytest.mark.parametrize(
    "replace,error",
    [
        ({"q_value": float("nan")}, "nonfinite"),
        (
            {
                "intervals": [
                    {"lower": 1.0, "upper": float("inf"), "confidence_level": 0.95}
                ]
            },
            "nonfinite",
        ),
        ({"q_value": 0.005}, "probabilities"),
    ],
)
@pytest.mark.wal
def test_parquet_invalid_statistics_fail_before_reservation(
    workspace_tmp_path,
    replace,
    error,
):
    from devbench.analysis.episode_evidence_queries import query_parquet_result

    root = workspace_tmp_path
    _, broker, _ = setup(root / "authority")
    artifact, open_artifact = tier_a_parquet(root, broker, replace=replace)
    before = broker.exposure("ctx", "tier-a")["possible_reservations"]
    with pytest.raises(ValueError, match=error):
        query_parquet_result(
            artifact,
            {"limit": 1},
            open_artifact=open_artifact,
            evidence_delivery=broker.delivery("ctx", "tier-a"),
        )
    assert broker.exposure("ctx", "tier-a")["possible_reservations"] == before


@pytest.mark.wal
def test_parquet_pages_recheck_entitlements_before_disclosure(workspace_tmp_path):
    from contextlib import contextmanager
    from io import BytesIO

    from devbench.analysis.episode_evidence_queries import query_parquet_result
    from devbench.analysis.evidence_access import AccessDenied, AccessPolicy
    from devbench.engine.workflow_operations import OperationBlocked

    root = workspace_tmp_path
    _, broker, _ = setup(root / "authority")
    artifact, open_artifact = tier_a_parquet(
        root,
        broker,
        adjusted=("q_local", "q_budget"),
    )
    policy = AccessPolicy("policy", 2, allow_outcomes=True)
    broker.register_policy(policy)
    broker.register_context(
        "limited",
        principal_id="scientist",
        lineage_id="study",
        role="controller",
        phase="analysis",
        policy_sha256=policy.sha256(),
        parents=("ctx",),
    )
    delivery = broker.delivery("limited", "tier-a")

    def query(args, **kwargs):
        return query_parquet_result(
            artifact,
            args,
            evidence_delivery=delivery,
            open_artifact=kwargs.pop("open_artifact", open_artifact),
            **kwargs,
        )

    first = query({"limit": 2})
    assert first["rows"][0]["effect"] == 2.0
    statistics = {"p_value", "q_value", "q_local", "q_budget", "intervals"}
    assert all(statistics.isdisjoint(row) for row in first["rows"])
    assert first["query_receipt"]["statistics_included"] is False
    continuation = first["query_receipt"]["next_cursor"]
    for forbidden in (
        {"sort_by": "q_value"},
        {"filters": {"p_value_lte": 0.1}},
        {"offset": 2},
        {"cursor": continuation, "limit": 3},
    ):
        with pytest.raises(AccessDenied):
            query(forbidden)
    before = broker.exposure("limited", "tier-a")["possible_reservations"]

    @contextmanager
    def changed_bytes(value):
        with open_artifact(value) as stream:
            yield BytesIO(stream.read() + b"tamper")

    with pytest.raises(ValueError, match="hash|identity"):
        query({"limit": 2, "cursor": continuation}, open_artifact=changed_bytes)

    invalidated = False

    @contextmanager
    def invalidate_use(value):
        nonlocal invalidated
        with open_artifact(value) as stream:
            yield stream
        invalidated = True

    def guard(_):
        if invalidated:
            raise OperationBlocked("adaptive_use_invalidated_during_delivery")

    with pytest.raises(
        OperationBlocked,
        match="adaptive_use_invalidated_during_delivery",
    ):
        query(
            {"limit": 2, "cursor": continuation},
            open_artifact=invalidate_use,
            _use_guard=guard,
        )
    assert broker.exposure("limited", "tier-a")["possible_reservations"] == before


@pytest.mark.wal
def test_parquet_extra_statistics_respect_field_allowlist(workspace_tmp_path):
    from devbench.analysis.episode_evidence_queries import query_parquet_result
    from devbench.analysis.evidence_access import AccessPolicy, _fields

    _, broker, _ = setup(workspace_tmp_path / "authority")
    artifact, open_artifact = tier_a_parquet(
        workspace_tmp_path,
        broker,
        adjusted=("q_local", "q_budget"),
    )
    full = query_parquet_result(
        artifact,
        {"limit": 1},
        open_artifact=open_artifact,
        evidence_delivery=broker.delivery("ctx", "tier-a"),
    )
    excluded = {"q_value", "q_budget", "confidence_level"}
    policy = AccessPolicy(
        "field-limited",
        1,
        allow_outcomes=True,
        allow_statistics=True,
        allowed_fields=tuple(sorted(_fields(full) - excluded)),
    )
    broker.register_policy(policy)
    broker.register_context(
        "field-limited",
        principal_id="scientist",
        lineage_id="study",
        role="controller",
        phase="analysis",
        policy_sha256=policy.sha256(),
    )
    limited = query_parquet_result(
        artifact,
        {"limit": 1},
        open_artifact=open_artifact,
        evidence_delivery=broker.delivery("field-limited", "tier-a"),
    )
    (row,) = limited["rows"]
    assert row["p_value"] == 0.01 and row["q_local"] == 0.05
    assert {"q_value", "q_budget", "intervals"}.isdisjoint(row)
    assert broker.verify_query_page(limited)
