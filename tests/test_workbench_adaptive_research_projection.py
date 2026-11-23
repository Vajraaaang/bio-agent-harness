"""Exercise investigation workflow readback through the Workbench HTTP boundary."""

import pytest

from devbench.platform import workbench_ui_projection
from devbench.platform.workbench_ui_projection import _adaptive_next_action
from tests.test_workbench_operator_runtime import operator  # noqa: F401

pytestmark = pytest.mark.wal


def test_investigation_shows_a_queued_step_without_revising_the_chat(
    request, monkeypatch
):
    source, fetch = request.getfixturevalue("operator")
    record = fetch(
        "/api/investigations",
        {
            "question": "What would per-capture quality control flag for review?",
            "source": str(source / "cells.h5ad"),
            "proposal_mode": "agent",
            "declarations": {
                "assay": "scrna",
                "measurement": "umi_counts",
                "count_provenance": "Synthetic software fixture",
                "capture_unit_key": "capture",
                "capture_units_confirmed": True,
                "annotation_provenance": "No cell labels are asserted",
            },
            "scientific_context": {
                "counts_layer": "counts",
                "biological_independence": {
                    "independent": True,
                    "unit_column": "donor",
                    "evidence": "Synthetic fixture assigns independent donors",
                },
            },
        },
    )
    sid = record["scientific_session_id"]
    investigation = f"/api/investigations/{record['id']}"
    before = fetch(investigation)
    assert before["adaptive_research"]["session_id"] == sid
    assert before["adaptive_research"]["plan"]["summary"]
    assert before["adaptive_research"]["steps"] == []

    curated = f"/api/scientific/single-cell/{sid}"
    options = fetch(curated)
    source_id = options["sources"][0]["id"]
    fetch(
        curated,
        {
            "action": "queue",
            "expected_revision": options["revision"],
            "step_id": "live-qc",
            "branch_id": "quality",
            "operation": "scrna_batch_qc",
            "biological_unit": "donor",
            "sources": {"expression": {"id": source_id, "port": None}},
            "parameters": {"batch_key": "capture"},
            "allow_data_execution": True,
        },
    )
    after = fetch(investigation)
    assert after["revision"] == before["revision"]
    projected = after["adaptive_research"]
    assert projected["revision"] > before["adaptive_research"]["revision"]
    assert projected["next_action"]["status"] == "needs_approval"
    [step] = projected["steps"]
    assert step["id"] == "live-qc"
    assert step["operation"] == "scrna_batch_qc"
    assert step["status"] == "ready"
    assert step["generation"] == 1
    assert "receipt_sha256" not in step
    assert projected["activity"]["events"] == [
        {
            "title": "Per-capture quality control",
            "detail": "Step queued for execution.",
            "status": "queued",
        },
    ]

    binding = fetch.app._adaptive_bindings[sid]
    ledger = binding.delivery.broker.authority.ledger

    def count_disclosures():
        return ledger._read(
            lambda db: db.execute(
                "SELECT count(*) FROM scientific_records "
                "WHERE lineage_id=? AND kind='disclosure_event'",
                (sid,),
            ).fetchone()[0]
        )

    disclosures = count_disclosures()
    fetch(investigation)
    assert count_disclosures() == disclosures
    fetch.app._adaptive_operations.add(sid)
    try:
        active = fetch(investigation)["adaptive_research"]
        assert active["status"] == "running"
        assert active["activity"]["current"]["status"] == "running"
        assert active["activity"]["events"][:-1] == projected["activity"]["events"]
        assert active["activity"]["events"][-1]["status"] == "running"
        assert count_disclosures() == disclosures
    finally:
        fetch.app._adaptive_operations.discard(sid)

    checked = {
        "status": "available",
        "answer": "The descriptive percentile is 5.40%.",
        "metrics": [{"label": "Mitochondrial percentage", "value": 5.4}],
        "limitations": ["The observation is scoped to this sample."],
    }
    monkeypatch.setattr(
        workbench_ui_projection, "_adaptive_findings", lambda *args: checked
    )
    answer = fetch(investigation)["adaptive_research"]
    assert answer["checked_observation"] == checked


def test_workbench_surfaces_campaign_hold_before_suggesting_another_model_step():
    view = {"actions": {"advance": True, "model_required": True}}
    label, detail, status = _adaptive_next_action(view, campaign_hold=True)
    assert label == "Review the stopped Codex campaign"
    assert status == "blocked"
    assert "before sending another request" in detail
