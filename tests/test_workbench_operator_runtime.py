"""Verify local Workbench session binding through its loopback HTTP API."""

import json
import os
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from devbench.platform.workbench_ui import WorkbenchService, make_server
from devharness_controlled.native_contracts import single_cell_kernel_images
from tests.test_thesis_aims import _aim
from tests.test_workbench_bulk_input_options import _transcode_tables
from tests.test_workbench_episode_intake import record as bulk_fixture

pytestmark = pytest.mark.wal


@pytest.fixture
def operator(workspace_tmp_path, request, monkeypatch):
    import numpy as np
    import pandas as pd
    from anndata import AnnData
    from scipy.sparse import csr_matrix

    source = workspace_tmp_path / "inputs"
    source.mkdir()
    sample = [f"synthetic-{index}" for index in range(64)]
    obs = pd.DataFrame(
        {"capture": ["left", "right"] * 32, "donor": sample}, index=sample
    )
    var = pd.DataFrame(index=["MT-ND1", *[f"gene-{index}" for index in range(47)]])
    counts = np.random.default_rng(73).poisson(4, size=(64, 48))
    cells = AnnData(csr_matrix(counts), obs=obs, var=var)
    cells.layers["counts"] = cells.X.copy()
    cells.write_h5ad(source / "cells.h5ad")
    (source / "bulk.csv").write_text("gene,S1,S2,S3,S4\nG1,1,3,4,2\n")
    model_options = {}
    if getattr(request, "param", None) == "model":
        from tests.test_rna_investigation_backend import protected_fake_cli_backend
        from tests.test_two_agent_investigation_v24 import decision

        action = decision(
            "declare_sweep", sweep_id="small-check",
            members=[{"step_id": "omitted", "omission_reason": "outside_scope"}],
        )
        backend = protected_fake_cli_backend(
            workspace_tmp_path, monkeypatch,
            f"answer=json.dumps({{'action': {action!r}}})",
        )
        model_options = {
            "scientific_workspace": backend.methods.root,
            "method_selector": backend.selector,
            "proposal_selector": backend.selector,
            "allow_model_execution": True, "model_data_scope": "synthetic",
        }
    app = WorkbenchService(
        workspace_tmp_path / "workbench", [source],
        enable_scientific=True, allow_data_execution=True,
        **model_options,
    )
    app.interactive_adaptive = True
    app.mounted_image_id = (
        os.environ.get("DEVBENCH_WORKBENCH_SMOKE_IMAGE")
        or sorted(single_cell_kernel_images())[0]
    )
    server = make_server(app, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"

    def fetch(path, body=None):
        request = Request(
            origin + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Origin": origin, "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=60) as response:
                view = json.load(response)
        except HTTPError as error:
            if error.code == 409:
                pytest.fail(error.read(4_096).decode())
            raise
        if view.get("status") == "operation_failed" and path == "/api/investigations":
            pytest.fail(str(app.investigations.get(view["id"]).get("error")))
        return view

    try:
        fetch.app = app
        yield source, fetch
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
        app.close()


@pytest.mark.parametrize("layer", ["counts", "X"])
def test_question_binds_manual_single_cell_and_thesis_without_a_model(operator, layer):
    source, fetch = operator
    result = fetch("/api/investigations", {
        "question": "Which cell populations show donor-level expression differences?",
        "source": str(source / "cells.h5ad"),
        "proposal_mode": "agent",
        "declarations": {
            "assay": "scrna", "measurement": "umi_counts",
            "count_provenance": "Synthetic software fixture",
            "capture_unit_key": "capture", "capture_units_confirmed": True,
            "annotation_provenance": "No cell labels are asserted",
        },
        "scientific_context": {
            "counts_layer": layer,
            "biological_independence": {
                "independent": True, "unit_column": "donor",
                "evidence": "Synthetic fixture gives each unit a distinct donor",
            },
        },
    })
    sid = result["scientific_session_id"]
    assert sid and sid != result["id"]
    assert result["workflow_mode"] == "adaptive/1"
    route = "/api/scientific/"
    adaptive = fetch(route + "adaptive/" + sid)
    assert adaptive["mode"] == "adaptive/1"
    assert adaptive["graph_status"] == "available"
    assert adaptive["actions"]["model_required"] is False
    curated = fetch(route + "single-cell/" + sid)
    assert curated["status"] == "available" and curated["actions"]["queue"] is True
    assert curated["sources"][0]["biological_unit"] == "donor"
    thesis = fetch(route + "thesis/" + sid)
    assert thesis["status"] == "available"
    assert thesis["actions"]["propose_aim"]["enabled"] is True
    request = {
        "action": "queue", "expected_revision": curated["revision"],
        "step_id": "qc-1", "branch_id": "quality",
        "operation": "scrna_batch_qc", "biological_unit": "donor",
        "sources": {"expression": {"id": curated["sources"][0]["id"], "port": None}},
        "parameters": {"batch_key": "capture", "min_genes": 1},
    }
    with pytest.raises(HTTPError) as denied:
        fetch(route + "single-cell/" + sid, {**request, "allow_data_execution": False})
    assert denied.value.code == 403
    queued = fetch(
        route + "single-cell/" + sid, {**request, "allow_data_execution": True}
    )
    assert queued["step_id"] == "qc-1"
    ready = fetch(route + "adaptive/" + sid)
    assert ready["actions"]["advance"] is True
    assert ready["raw_matrix_rows"] == 0
    if os.environ.get("DEVBENCH_WORKBENCH_SMOKE_IMAGE") and layer == "X":
        dispatched = fetch(route + "adaptive/" + sid, {
            "action": "advance", "expected_revision": ready["revision"],
            "allow_data_execution": True, "allow_model_execution": False,
        })
        assert dispatched["steps_dispatched"] == 1
        completed = fetch(route + "single-cell/" + sid)
        if completed["steps"][0]["status"] != "succeeded":
            executor = fetch.app._adaptive_bindings[sid].tier_a_executor
            [receipt] = list(executor.runs.glob("*/io.json"))
            record = json.loads(receipt.read_text())
            pytest.fail(repr({"reason": record["reason"], "tool": record["tool"]}))
        assert completed["steps"][0]["qc_report_available"] is True
        detail = fetch(route + "single-cell/" + sid + "?qc_step=qc-1&generation=1")
        assert detail["step_id"] == "qc-1"
        assert detail["generation"] == 1
        assert detail["report"]["cells_input"] == 64
        assert "excluded_cell_ids" not in detail["report"]


def test_bulk_de_question_binds_aims_and_keeps_missing_design_explicit(operator):
    source, fetch = operator
    result = fetch("/api/investigations", {
        "question": "Which genes are differentially expressed after treatment?",
        "source": str(source / "bulk.csv"),
        "proposal_mode": "agent",
        "declarations": {
            "assay": "bulk", "measurement": "non_umi_counts",
            "count_provenance": "Synthetic count table, no design attached",
        },
    })
    sid = result["scientific_session_id"]
    assert sid
    adaptive = fetch("/api/scientific/adaptive/" + sid)
    assert adaptive["graph_status"] == "available"
    assert adaptive["actions"]["advance"] is False
    assert fetch("/api/scientific/single-cell/" + sid)["status"] == "available"
    thesis = fetch("/api/scientific/thesis/" + sid)
    assert thesis["actions"]["propose_aim"]["enabled"] is True
    assert thesis["clinical_release_allowed"] is False
    definition = _aim("bulk-design").model_dump(mode="json")
    fetch("/api/scientific/thesis/" + sid, {
        "action": "propose_aim",
        "expected_revision": thesis["revision"],
        "definition": definition,
    })
    pending = fetch("/api/scientific/thesis/" + sid)
    assert pending["aims"][0]["status"] == "operator_review"
    assert definition["question"] not in json.dumps(pending)


@pytest.mark.parametrize("compression", [None, ".csv.gz", ".tsv.gz"])
def test_declared_bulk_count_set_binds_governed_inputs_from_http(operator, compression):
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow

    source, fetch = operator
    fixture = bulk_fixture(source)
    if compression:
        _transcode_tables(fixture, compression)
    body = {
        key: fixture[key]
        for key in ("sources", "source_declarations", "scientific_context")
    }
    result = fetch("/api/investigations", {
        **body,
        "question": "Which genes are differentially expressed between B and A?",
        "proposal_mode": "agent",
    })
    sid = result["scientific_session_id"]
    assert result["workflow_mode"] == "adaptive/1"
    assert fetch("/api/scientific/adaptive/" + sid)["status"] == "available"
    binding = fetch.app._adaptive_bindings[sid]
    flow = AdaptiveWorkflow(binding.delivery.broker.authority.ledger, sid)
    artifacts = flow._read_projection(lambda db: flow._state(db).artifacts.values())
    assert {item[0]["representation"] for item in artifacts} == {
        "raw_counts_h5", "metadata_csv", "family_spec_json",
    }
    assert flow.authority.attempts(sid) == []


@pytest.mark.skipif(
    os.environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1",
    reason="Local protected FakeCLI capture requires opt-in",
)
@pytest.mark.parametrize("operator", ["model"], indirect=True)
def test_chat_opens_single_operator_model_after_fresh_scoped_consent(operator):
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    from devbench.study.evidence import decode_json

    source, fetch = operator
    result = fetch("/api/investigations", {
        "question": "Which bulk comparisons can this fixture support?",
        "source": str(source / "bulk.csv"),
        "proposal_mode": "agent",
        "declarations": {
            "assay": "bulk", "measurement": "non_umi_counts",
            "count_provenance": "Synthetic counts with no design attached",
        },
        "scientific_context": {"data_classification": "synthetic"},
    })
    sid = result["scientific_session_id"]
    endpoint = "/api/scientific/adaptive/" + sid
    available = fetch(endpoint)
    assert available["actions"]["model_available"] is True
    assert available["campaign_mode"] is None
    fetch(endpoint, {
        "action": "advance", "expected_revision": available["revision"],
        "allow_data_execution": True, "allow_model_execution": True,
    })
    completed = fetch(endpoint)
    assert completed["campaign_mode"] == "local_interactive_codex"
    assert completed["qualification"] == "not_established"
    controller = fetch.app._adaptive_bindings[sid].controller
    _, ledger, backend, campaign = controller.invoker._runtime()
    reservations = ledger.campaign_reservations(campaign["campaign_id"])
    assert len(reservations) == 1 and reservations[0]["status"] == "completed"
    [applied] = AdaptiveWorkflow(ledger, sid).controller_state()["decisions"]
    assert applied["result"]["status"] == "applied"
    assert applied["decision"]["kind"] == "declare_sweep"
    assert applied["evidence"]["controller_disclosure_reservation_id"]
    binding = fetch.app._adaptive_bindings[sid]
    broker = binding.delivery.broker
    with ledger._database() as db:
        intake = broker._source(db, binding.delivery.source_id)
        decision_source = broker._source(
            db, applied["evidence"]["controller_disclosure_source_id"]
        )
        assert decision_source["roots"] == intake["roots"]
    capture = backend._document(
        reservations[0]["capture"]["capture_ref"],
        artifact_type="rna_investigation_capture",
    )
    payload = decode_json(
        backend._artifact(capture["files"]["actor/context.json"], max_bytes=128000)
    )
    assert payload["task"]["scientific_objective"] == (
        "Which bulk comparisons can this fixture support?"
    )
    assert payload["dataset_summary"]["observed"]["expression_values_read"] is False
    assert set(payload["graph"]) == {"mode", "steps", "sweep_members"}
    assert not set(payload) & {"prior_state", "scientific_state", "query_results"}


def test_operator_bulk_requires_separate_local_reviews_before_admission(operator):
    from devbench.engine.workflow_operations import OperationBlocked
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    from devbench.platform.workbench_interactive import operator_bulk_actions

    source, fetch = operator
    fixture = bulk_fixture(source)
    fixture["scientific_context"]["bulk_episode_declarations"]["batch_columns"] = "none"
    created = fetch(
        "/api/investigations",
        {
            **{
                key: fixture[key]
                for key in ("sources", "source_declarations", "scientific_context")
            },
            "question": "Which genes are differentially expressed between B and A?",
            "proposal_mode": "agent",
        },
    )
    sid = created["scientific_session_id"]
    path = "/api/scientific/adaptive/" + sid

    def mutate(name, **fields):
        before = fetch(path)
        try:
            fetch(path, {
                "action": name, "expected_revision": before["revision"],
                "allow_data_execution": True, **fields,
            })
        except HTTPError as error:
            pytest.fail(error.read(4096).decode())
        return fetch(path)

    assert fetch(path)["operator_bulk"] == {"queue": True}
    checked = mutate("queue_operator_bulk")
    assert checked["operator_bulk"]["review"] == ["design"]
    metrics = checked["operator_bulk"]["preflight"]
    assert metrics["rank"] == metrics["columns"]
    assert metrics["residual_df"] > 0
    binding = fetch.app._adaptive_bindings[sid]
    flow = AdaptiveWorkflow(binding.delivery.broker.authority.ledger, sid)
    step_id = checked["operator_bulk"]["step_id"]
    actions = operator_bulk_actions(
        flow,
        binding.tier_a_executor,
        fetch.app.scientific.planner(),
        step_id,
    )
    with pytest.raises(
        OperationBlocked, match="completed_current_design_review_required"
    ):
        actions.prepare_research_admission(flow, step_id)
    assessment = {
        "initial": "The pinned structural diagnostic supports this directed contrast.",
        "final": "The declared scope is suitable for this provisional research fit.",
    }
    designed = mutate(
        "review_operator_bulk", touchpoint="design", assessment=assessment
    )
    assert designed["operator_bulk"]["review"] == ["publication"]
    with pytest.raises(
        OperationBlocked,
        match="completed_current_publication_review_required",
    ):
        actions.prepare_research_admission(flow, step_id)
    reviewed = mutate(
        "review_operator_bulk",
        touchpoint="publication",
        assessment=assessment,
    )
    assert reviewed["operator_bulk"]["completed_reviews"] == ["design", "publication"]
    assert reviewed["operator_bulk"]["run"] is True
    assert reviewed["actions"]["run_operator_steps"] == [
        {"step_id": step_id, "generation": 1}
    ]
    actions.prepare_research_admission(flow, step_id)
    assert not flow.authority.attempts(sid)


def test_exploratory_follow_up_keeps_the_current_planner_session(operator):
    source, fetch = operator
    created = fetch("/api/investigations", {
        "question": "Which cell populations show donor-level expression differences?",
        "source": str(source / "cells.h5ad"),
        "proposal_mode": "agent",
        "declarations": {
            "assay": "scrna", "measurement": "umi_counts",
            "count_provenance": "Synthetic software fixture",
            "capture_unit_key": "capture", "capture_units_confirmed": True,
            "annotation_provenance": "No cell labels are asserted",
        },
        "scientific_context": {
            "counts_layer": "counts", "biological_independence": None,
        },
    })
    sid = created["scientific_session_id"]
    assert sid
    curated = fetch("/api/scientific/single-cell/" + sid)
    assert curated["sources"][0]["biological_unit"] == "unverified_exploratory"
    followed = fetch("/api/investigations/" + created["id"], {
        "action": "follow_up", "expected_revision": created["revision"],
        "question": "Which other cell populations show expression differences?",
    })
    assert followed["scientific_session_id"] == sid
    assert followed["id"] != created["id"]
    assert len(fetch("/api/scientific")["plans"]) == 1


def test_verified_cellranger_header_registers_exact_droplet_input(operator):
    from dataclasses import asdict

    from tests.test_p3_scrna_droplets import _provenance, _write_tenx

    source, fetch = operator
    path = source / "raw_feature_bc_matrix.h5"
    _write_tenx(path)
    result = fetch("/api/investigations", {
        "question": "Which cell populations show expression differences?",
        "source": str(path),
        "proposal_mode": "agent",
        "declarations": {
            "assay": "scrna", "measurement": "umi_counts",
            "count_provenance": "Synthetic upstream CellRanger count fixture",
            "capture_units_confirmed": True,
            "annotation_provenance": "No cell labels are asserted",
        },
        "scientific_context": {
            "biological_independence": None,
            "raw_droplet_provenance": asdict(_provenance(path)),
        },
    })
    sid = result["scientific_session_id"]
    assert sid
    curated = fetch("/api/scientific/single-cell/" + sid)
    assert {row["representation"] for row in curated["sources"]} == {
        "raw_cellranger_v3_h5", "raw_droplet_provenance_json",
    }
    assert all(
        row["biological_unit"] == "unverified_exploratory" for row in curated["sources"]
    )
