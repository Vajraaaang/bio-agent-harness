"""Bind compact within-sample findings to the current Single-Cell QC receipt."""

from __future__ import annotations

import csv
import io
import math
import re
from statistics import StatisticsError, correlation, quantiles

from devbench.analysis.checkpoints import digest
from devbench.engine.workflow_operations import OperationConflict

from .adaptive_workflow import AdaptiveWorkflow
from .workbench_adaptive import _graph_revision

_MAX_BYTES = 4_000_000
_MAX_ROWS = 20_000


def _rows(raw):
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8")), strict=True)
    if not {"n_genes_by_counts", "pct_counts_mt", "qc_pass"} <= set(
        reader.fieldnames or ()
    ):
        raise ValueError("QC metrics lack required descriptive fields")
    rows = []
    for row in reader:
        genes = int(row["n_genes_by_counts"])
        fraction = float(row["pct_counts_mt"])
        passed = row["qc_pass"]
        if not (
            len(rows) < _MAX_ROWS and genes >= 0 and math.isfinite(fraction)
            and 0 <= fraction <= 100 and passed in {"True", "False"}
        ):
            raise ValueError("QC metrics exceed their bounds or contain invalid facts")
        rows.append((genes, fraction, passed == "True"))
    return rows


def _mitochondrial_summary(rows):
    if len(rows) < 8:
        return None
    genes, fraction, _ = zip(*rows, strict=True)
    cuts = quantiles(genes, n=4, method="inclusive")
    lowest = [y for x, y in zip(genes, fraction, strict=True) if x <= cuts[0]]
    highest = [y for x, y in zip(genes, fraction, strict=True) if x > cuts[-1]]
    if len(lowest) < 2 or len(highest) < 2:
        return None
    try:
        rho = correlation(genes, fraction, method="ranked")
    except StatisticsError:
        return None
    return {
        "lowest_n": len(lowest),
        "highest_n": len(highest),
        "lowest_p95": round(quantiles(lowest, n=100, method="inclusive")[94], 2),
        "highest_p95": round(quantiles(highest, n=100, method="inclusive")[94], 2),
        "spearman_rho": round(rho, 3),
    }


def _current_step(flow, connection):
    graph = flow.projection_snapshot(connection=connection)
    candidates = [
        row for row in graph["steps"]
        if row["operation_id"] == "scrna_batch_qc" and row["status"] == "succeeded"
    ]
    # Several reviewed or comparison branches need an explicit evidence selection.
    if len(candidates) != 1:
        return None
    row = candidates[0]
    return row["step_id"], row["generation"]


def _question_metric(question):
    return (
        re.search(r"mitochondri|\bmito\b", question, flags=re.IGNORECASE)
        and re.search(
            r"complexity|detected (?:features|genes)", question, re.IGNORECASE
        )
    )


def _interpret(report, measures):
    total, kept = report["cells_input"], report["cells_retained"]
    flagged = report["cells_excluded"]
    answer = (
        f"Per-capture QC flagged {flagged:,} of {total:,} profiles "
        f"({100 * flagged / total:.1f}%) for review."
    )
    if measures is not None:
        answer = (
            "The lowest detected-gene quartile had a mitochondrial UMI "
            f"95th percentile of {measures['lowest_p95']:.2f}% versus "
            f"{measures['highest_p95']:.2f}% in the highest quartile. "
            f"Overall rank association was {measures['spearman_rho']:+.2f}. " + answer
        )
    metrics = [
        {"label": "Input profiles", "value": total},
        {"label": "Flagged for review", "value": flagged},
        {"label": "Passed the diagnostic", "value": kept},
    ]
    return answer, metrics


def _read_metrics(binding, flow, step_id, generation, detail, question):
    from .workbench_curated_actions import _deliver, _qc_report
    from .workflow_adaptive_actions import register_adaptive_output_source

    delivery = binding.delivery
    with flow._read_connection() as db:
        revision = _graph_revision(flow, connection=db)
        artifact = flow._current_use(db, step_id, "qc_metrics", "compute", generation)
    source_id, dependencies = register_adaptive_output_source(
        flow, step_id, "qc_metrics", (),
        broker=delivery.broker, context_id=delivery.context_id, role="scientist",
    )
    with binding.tier_a_executor.open_output(artifact) as stream:
        raw = stream.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES:
        return None, None
    rows = _rows(raw)
    report = detail["report"]
    if len(rows) != report["cells_input"] or sum(r[2] for r in rows) != report[
        "cells_retained"
    ]:
        raise ValueError("QC metrics disagree with the current report denominators")
    measures = _mitochondrial_summary(rows) if _question_metric(question) else None

    def guard(db):
        if _graph_revision(flow, connection=db) != revision or flow._current_use(
            db, step_id, "qc_metrics", "compute", generation
        ) != artifact:
            raise OperationConflict("QC evidence changed during findings readback")
        current = _qc_report(flow, db, flow._state(db), step_id, generation)
        if current[1] != detail["receipt_sha256"]:
            raise OperationConflict("QC report changed during findings readback")
        delivery.broker._query_dependencies(db, tuple(dependencies))

    payload = {
        "question_sha256": digest(question),
        "receipt_sha256": detail["receipt_sha256"],
        "observations": len(rows),
        "measurements": measures,
    }
    _deliver(delivery, source_id, payload, guard)
    return measures, artifact.sha256


def current_qc_findings(app, binding, question):
    """Show observations from verified, current receipts with bounded readback."""
    from .workbench_curated_actions import _qc_detail

    if binding.tier_a_executor is None:
        return None
    _, ledger = binding.require_owner(app.scientific)
    flow = AdaptiveWorkflow(ledger, binding.session_id)
    selected = flow._read_projection(lambda db: _current_step(flow, db))
    if selected is None:
        return None
    step_id, generation = selected
    detail = _qc_detail(app, binding, flow, step_id, generation)
    report = detail["report"]
    if type(report.get("cells_input")) is not int or report["cells_input"] <= 0:
        raise ValueError("QC report has no valid input denominator")
    measures, metrics_sha256 = _read_metrics(
        binding, flow, step_id, generation, detail, question
    )
    answer, metrics = _interpret(report, measures)
    limitations = [
        "This is a descriptive QC observation within the attached sample.",
        "A diagnostic flag alone does not establish cell damage or justify exclusion.",
    ]
    if _question_metric(question) and measures is None:
        limitations.append("The available QC metrics do not support this comparison.")
    evidence = [{"label": "Current QC receipt", "sha256": detail["receipt_sha256"]}]
    if metrics_sha256:
        evidence.append({"label": "Bound QC metrics", "sha256": metrics_sha256})
    return {
        "status": "available",
        "answer": answer,
        "summary": "Checked observation from the current per-capture QC run.",
        "metrics": metrics,
        "evidence": evidence,
        "limitations": limitations,
    }
