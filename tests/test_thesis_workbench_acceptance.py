"""Thesis acceptance for the local Workbench and measurement gates."""

import csv
import hashlib
import io
import json

import numpy as np
import pandas as pd
import pytest
from PIL import Image

from devbench.analysis.evidence_access import EvidenceBroker
from devbench.core.rna_answerability import (
    AnswerabilityRequest,
    AttestedMeasurement,
    assess_answerability,
)
from devbench.platform.workbench_ui import WorkbenchService
from tests.test_scientific_workspace_http import call, running

_QUESTION = "How many genes are expressed per sample and is there a batch effect?"
_MEASUREMENT = "normalized_expression"


def _case_control_source(root, *, file_type, confounded=False):
    samples = [f"s{i}" for i in range(8)]
    features = ["batch_signal", "condition_signal", "sparse_signal"]
    condition = np.array([-1] * 4 + [1] * 4)
    batch = condition.copy() if confounded else np.tile([-1, 1], 4)
    residual = np.tile([-1, -1, 1, 1], 2)
    values = np.column_stack((
        12 + 2 * batch + 3 * condition + residual,
        12 + 2 * condition + residual,
        1 + batch,
    ))
    obs = pd.DataFrame({
        "condition": np.where(condition < 0, "control", "case"),
        "batch": np.where(batch < 0, "A", "B"),
    }, index=samples)
    if file_type == "h5ad":
        from anndata import AnnData

        path = root / "expression.h5ad"
        AnnData(values, obs=obs, var=pd.DataFrame(index=features)).write_h5ad(path)
        request = {"source": str(path), "declarations": {"measurement": _MEASUREMENT}}
    else:
        path = root / "expression.csv"
        metadata = root / "metadata.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["gene_id", *samples])
            writer.writerows(
                [gene, *row] for gene, row in zip(features, values.T, strict=True)
            )
        obs.to_csv(metadata, index_label="sample_id")
        request = {
            "sources": [str(path), str(metadata)],
            "source_declarations": [
                {"role": "expression", "measurement": _MEASUREMENT},
                {"role": "metadata"},
            ],
        }
    return {"question": _QUESTION, **request}, samples, values


def _open_workbench(workspace):
    inputs = workspace / "inputs"
    inputs.mkdir()
    return WorkbenchService(
        workspace / "service", [inputs],
        enable_scientific=True, allow_data_execution=True,
    ), inputs


def _check_exposure(app, lineage_id, audit):
    ledger = app.scientific.open_workflow_ledger()
    broker = EvidenceBroker.open_existing(ledger.scientific)
    reservation = broker.reservation(audit["disclosure_reservation_id"])
    assert reservation["state"] == "delivery_attempted"
    assert reservation["source_id"] == audit["evidence_source_id"]
    assert reservation["context_id"] == audit["evidence_context_id"]
    result_sha = reservation["content_sha256"]
    sealed = ledger.scientific.get(
        lineage_id, f"matrix-audit-{audit['audit_id']}-matrix_audit_completed",
    )
    assert sealed["record"]["state"]["result_sha256"] == result_sha
    assert sealed["record"]["state"]["exposure_basis_sha256"] == (
        audit["exposure_basis_sha256"]
    )


def _download_checked(port, descriptor, *, name, media_type):
    assert descriptor["name"] == name
    assert descriptor["url"].startswith("/api/investigations/")
    assert descriptor["url"].endswith(f"/{descriptor['sha256']}/{name}")
    status, body, content_type = call(port, descriptor["url"])
    assert status == 200, body[:200]
    assert content_type.startswith(media_type)
    assert hashlib.sha256(body).hexdigest() == descriptor["sha256"]
    return body


@pytest.mark.wal
@pytest.mark.parametrize("file_type", ["h5ad", "csv"])
def test_workbench_autonomously_publishes_bound_batch_diagnostic(
    workspace_tmp_path, file_type,
):
    app, inputs = _open_workbench(workspace_tmp_path)
    try:
        request, samples, values = _case_control_source(inputs, file_type=file_type)
        record = app.investigations.create(request)
        assert record["status"] == "completed", record.get("error")
        assert not record.get("clarification_fields")
        audit = record["descriptive_matrix_audit"]
        assert audit["nonzero_per_sample"] == [
            {"sample_id": sample, "nonzero_genes": int(np.count_nonzero(row))}
            for sample, row in zip(samples, values, strict=True)
        ]
        assert audit["batch"]["adjusted_for"] == "condition"
        partial = {
            row["gene_id"]: row["batch_partial_r2"] for row in audit["batch"]["genes"]
        }
        # Orthogonal columns yield batch variance 4 and residual variance 1.
        assert partial["batch_signal"] == pytest.approx(0.8)
        assert partial["condition_signal"] == pytest.approx(0.0, abs=1e-12)
        assert {row["tool"] for row in audit["tool_receipts"] if row["succeeded"]} == {
            "audit_expression_quality", "analyze_batch_effects",
        }
        _check_exposure(app, record["id"], audit)

        plots = audit["plot_artifacts"]
        assert plots["plot_state"]["render_verified"] is True
        assert plots["plot_state"]["scientific_assessment"] == "pending"
        with running(app) as port:
            status, data, _ = call(port, f"/api/investigations/{record['id']}")
            assert status == 200
            visible = next(
                turn["content"] for turn in json.loads(data)["turns"]
                if turn["kind"] == "dataset_answer"
            )
            assert visible["plot_artifacts"] == plots
            image = _download_checked(
                port, plots["figure"], name="batch-pca.png", media_type="image/png",
            )
            sidecar = _download_checked(
                port, plots["numeric_sidecar"], name="batch-pca.json",
                media_type="application/json",
            )
            bad_url = plots["numeric_sidecar"]["url"].replace(
                plots["numeric_sidecar"]["sha256"], "f" * 64,
            )
            assert call(port, bad_url)[0] == 404
            if file_type == "h5ad":
                retained = (
                    app.workspace / "matrix-audit-artifacts"
                    / plots["numeric_sidecar"]["sha256"]
                )
                retained.chmod(0o600)
                retained.write_bytes(sidecar[:-1] + b"]")
                assert call(port, plots["numeric_sidecar"]["url"])[0] == 409
        with Image.open(io.BytesIO(image)) as decoded:
            assert decoded.format == "PNG"
            decoded.verify()
        numeric = json.loads(sidecar)
        assert numeric["pca"] == audit["batch"]["pca"]
        assert {
            row["gene_id"]: row["batch_partial_r2"] for row in numeric["batch"]["genes"]
        } == partial
        assert "expression" not in numeric
    finally:
        app.close()


@pytest.mark.wal
def test_workbench_reports_absent_plot_for_confounded_design(workspace_tmp_path):
    app, inputs = _open_workbench(workspace_tmp_path)
    try:
        request, _, _ = _case_control_source(inputs, file_type="h5ad", confounded=True)
        record = app.investigations.create(request)
        assert record["status"] == "completed", record.get("error")
        audit = record["descriptive_matrix_audit"]
        assert audit["batch"]["status"] == "not_identifiable"
        assert all(row["batch_partial_r2"] is None for row in audit["batch"]["genes"])
        plots = audit["plot_artifacts"]
        assert plots["plot_state"]["rendered"] is False
        assert plots["reason"] == "batch_comparison_not_identifiable"
        assert plots["figure"] is plots["numeric_sidecar"] is None
        _check_exposure(app, record["id"], audit)
    finally:
        app.close()


@pytest.mark.parametrize(
    ("family", "question", "status", "reason", "missing"),
    [
        (
            15, "rna_velocity", "awaiting_information", "missing_measurements",
            (("spliced_counts",), ("unspliced_counts",)),
        ),
        (21, "unfamiliar_question", "refused", "unregistered_question", ()),
    ],
)
def test_counts_only_investigation_refuses_unsupported_inference(
    family, question, status, reason, missing,
):
    request = AnswerabilityRequest(
        family_id=family, question=question, assay="single_cell_rna_seq",
        entry_point="annotated_object",
    )
    evidence = AttestedMeasurement(
        kind="gene_counts", artifact_sha256="1" * 64, receipt_sha256="2" * 64,
        observation_axis_sha256="3" * 64, feature_axis_sha256="4" * 64,
    )
    assessed = assess_answerability(request, measurements=(evidence,))
    assert assessed.status == status
    assert assessed.reason == reason
    assert assessed.missing_measurements == missing
    assert assessed.execution_route is None
    assert assessed.clinical_release_allowed is False
