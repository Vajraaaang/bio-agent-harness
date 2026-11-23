"""Exercise question intake and disclosure through the loopback HTTP server."""

import json
import threading
import time
from urllib.request import Request, urlopen

import pytest

from devbench.platform.workbench_ui import WorkbenchService, make_server
from tests.test_workbench_descriptions import fixture_h5ad


@pytest.fixture
def chat(workspace_tmp_path, request):
    inputs = workspace_tmp_path / "inputs"
    inputs.mkdir()
    source = fixture_h5ad(inputs / "cells.h5ad")
    app = WorkbenchService(
        workspace_tmp_path / "workbench", [inputs],
        enable_scientific=getattr(request, "param", False),
    )
    server = make_server(app, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"

    def fetch(path, body=None):
        headers = {"Origin": origin, "Content-Type": "application/json"}
        request = Request(
            origin + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
        )
        with urlopen(request, timeout=5) as response:
            return json.load(response)

    def ask(question, *, attach=True):
        body = {"question": question, "proposal_mode": "agent"}
        if attach:
            body["source"] = str(source)
        value = fetch("/api/investigations", body)
        deadline = time.monotonic() + 10
        while value.get("operation_pending") and time.monotonic() < deadline:
            time.sleep(0.02)
            value = fetch("/api/investigations/" + value["id"])
        assert not value.get("operation_pending")
        return value

    try:
        yield ask
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
        app.close()


@pytest.mark.parametrize("question", [
    "Which genes are differentially expressed?",
    "Which are the differentially expressed genes?",
    "Show detected gene counts alongside library size and mitochondrial QC.",
])
def test_general_analysis_does_not_require_bounded_matrix_count(chat, question):
    record = chat(question)
    request = record["request"]
    assert request["remaining_action"] in (
        "inferential_analysis", "exploratory_computation",
    )
    assert request["matrix_computation_required"] is False
    assert record.get("required_action") != "bounded_matrix_computation"


@pytest.mark.parametrize("question", [
    "Inspect droplet barcode ranks and check which inputs qualify for EmptyDrops.",
    "Review the requirements for per-capture MAD QC and lane doublets.",
    "Which batch mixing and biological conservation checks "
    "are supported by these data?",
    "Can we compare donor-level pseudobulk states with "
    "the recorded samples and labels?",
    "Does the donor design support a relative composition analysis with scCODA?",
    "Audit design rank, donor pairing and latent batch structure.",
    "Which condition contrasts and pathway tests (CAMERA or fgsea) "
    "do the data support?",
    "Which columns identify biological units and groups?",
    "What quality checks are appropriate for this dataset?",
    "Which comparisons does this dataset support?",
    "Which quality concerns can the saved evidence and supported workflow "
    "help inspect in this dataset?",
    "What can the supported batch diagnostics establish for this dataset, "
    "and what remains uncertain?",
    "Which supported methods could address a follow-up question with this dataset, "
    "and what additional evidence or metadata would they require?",
    "Can I compare DESeq2 vs edgeR for this experiment?",
    "What cell types express CD3D?",
    "Which pathways are enriched?",
])
def test_supported_question_phrasing_routes_without_intent_clarification(
    chat, question,
):
    record = chat(question)
    assert record["request"]["remaining_action"] != "clarify_intent"
    assert record.get("required_action") != "bounded_matrix_computation"


def test_pinned_description_keeps_read_only_summary_and_table_title(chat):
    record = chat(
        "How many cells and genes are stored?\n\nPinned draft context:\n"
        "• @aim aim-1 (generation 1)\n• @step plan-2"
    )
    assert record["status"] == "completed"
    assert record["turns"] == record["thread_turns"]
    assert "summary" in record["synthesis"]["supported_findings"]
    turn = next(item for item in record["turns"] if item["kind"] == "dataset_answer")
    assert turn["content"]["tables"][0]["table_title"] == "Stored dimensions"


def test_no_input_records_a_data_requirements_response(chat):
    record = chat("Which genes changed after treatment?", attach=False)
    assert record["status"] == "awaiting_information"
    assert record["required_action"] == "attach_dataset"
    assert record["source"] is None
    assert record["scientific_session_id"] is None
    turn = next(item for item in record["turns"] if item["kind"] == "clarification")
    assert "biological" in turn["content"]["guidance"]
    assert record["turns"] == record["thread_turns"]


def test_matrix_audit_reports_disabled_workspace(chat):
    record = chat("Run PCA\n\nPinned draft context:\n• @selection selection-1")
    assert record["required_action"] == "scientific_workspace_not_enabled"
    assert record["diagnostic_code"] == "scientific_workspace_not_enabled"


@pytest.mark.wal
@pytest.mark.parametrize("chat", [True], indirect=True)
def test_matrix_audit_reports_disabled_data_execution(chat):
    record = chat("Run PCA")
    assert record["diagnostic_code"] == "data_execution_disabled"
    assert not record.get("descriptive_matrix_audit")


def test_scientific_startup_checks_the_loaded_wal_runtime(
    workspace_tmp_path, monkeypatch,
):
    import sqlite3

    inputs = workspace_tmp_path / "inputs"
    inputs.mkdir()
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 43, 1))
    with pytest.raises(RuntimeError, match=r"3\.43\.1.*3\.51\.3.*DYLD_LIBRARY_PATH"):
        WorkbenchService(
            workspace_tmp_path / "workbench",
            [inputs],
            enable_scientific=True,
        )
