"""Bounded local descriptive matrix tools and their disclosure receipts."""

import csv
import json
import tempfile
import uuid
from pathlib import Path

from devbench.analysis.checkpoints import digest
from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker
from devbench.analysis.rna_input_set_contract import (
    COUNT_MEASUREMENTS,
    MAX_SOURCE_BYTES,
)
from devbench.analysis.workbench_episode_intake import (
    MatrixIntakeNeedsInformation,
    inspect_matrix_intake,
)
from devbench.engine.workflow_operations import OperationConflict
from devharness_core.provenance import copy_artifact
from devharness_server.dispatcher import ToolDispatcher, capture_tool_events

from .workbench_matrix_audit_plot import (
    ARTIFACT_MEDIA,
    read_matrix_plot_blob,
    retain_matrix_audit_plot,
)

AUDIT_FORMAT = "devbench.workbench-matrix-audit/1"
_PCA_INTENTS = {"pca", "batch_pca"}
_BATCH_INTENTS = {"batch_association", "batch_pca"}
_DISCLOSURE_FIELDS = {
    "evidence_context_id",
    "evidence_source_id",
    "disclosure_reservation_id",
    "exposure_basis_sha256",
}


class MatrixAuditNeedsMeasurement(MatrixIntakeNeedsInformation):
    """A structural intake without an expression-scale declaration."""

    def __init__(self, intake, record):
        reason = "Confirm the measurement scale for the selected matrix layer."
        field = {
            "field": "measurement",
            "label": "Measurement scale",
            "type": "select",
            "required": True,
            "reason": reason,
            "options": [
                {"value": "non_umi_counts", "label": "Raw non-UMI counts"},
                {"value": "umi_counts", "label": "Raw UMI counts"},
                {"value": "normalized_expression", "label": "Normalized expression"},
            ],
        }
        if record.get("sources"):
            index = intake["source_roles"]["expression"]
            field.update(scope="source_declarations", source_index=index,
                         source=record["sources"][index])
        super().__init__(reason, [field])
        self.fact_provenance = intake["fact_provenance"]
        self.warnings = [
            "Measurement provenance is unknown from the header, sample IDs "
            "and layer name.",
            "The registered quality audit uses scale-dependent summaries.",
            "Raw counts require normalization and a log transform before batch PCA.",
        ]


def _result_sha256(audit):
    return digest(
        {key: value for key, value in audit.items() if key not in _DISCLOSURE_FIELDS}
    )


def verify_matrix_audit_disclosure(broker, lineage, audit):
    context_id, source_id = audit["evidence_context_id"], audit["evidence_source_id"]
    reservation_id = audit["disclosure_reservation_id"]
    lineage_id = lineage["lineage_id"]
    broker.check_delivery(
        context_id, source_id, lineage_id=lineage_id, role="scientist"
    )
    reservation = broker.reservation(reservation_id)
    result_sha = _result_sha256(audit)
    sealed = broker.authority.get(
        lineage_id,
        f"matrix-audit-{audit['audit_id']}-matrix_audit_completed",
    )
    if (
        audit.get("intake_sha256") != lineage["intake_sha256"]
        or reservation["context_id"] != context_id
        or reservation["source_id"] != source_id
        or reservation["state"] not in {"delivery_attempted", "receipt_confirmed"}
        or reservation["content_sha256"] != result_sha
        or sealed is None
        or sealed["record"]["state"]
        != {
            "audit_id": audit["audit_id"],
            "intake_sha256": lineage["intake_sha256"],
            "intake_exposure_sha256": lineage["exposure_basis_sha256"],
            "result_sha256": result_sha,
            "exposure_basis_sha256": audit["exposure_basis_sha256"],
            "disclosure_reservation_id": reservation_id,
        }
    ):
        raise OperationConflict(
            "Descriptive matrix result has no matching disclosure receipt"
        )


def read_matrix_audit_artifact(app, investigation_id, audit_id, sha256, name):
    """Read a current, disclosed diagnostic artifact by exact identity."""
    from .workbench_ui_projection import _publication_hashes

    if name not in ARTIFACT_MEDIA:
        raise KeyError("Matrix diagnostic artifact is unavailable")
    # InvestigationService.get verifies the retained intake and audit disclosure.
    record = app.investigations.get(investigation_id)
    audit = record.get("descriptive_matrix_audit")
    if audit is None or audit["audit_id"] != audit_id:
        raise KeyError("Matrix diagnostic result is unavailable")
    role = "figure" if name == "batch-pca.png" else "numeric_sidecar"
    reference = (audit.get("plot_artifacts") or {}).get(role)
    if reference is None or (reference["name"], reference["sha256"]) != (name, sha256):
        raise KeyError("Matrix diagnostic artifact is unavailable")
    result_sha256 = _result_sha256(audit)
    profiles = record.get("source_profiles") or [record["profile"]]
    protected = _publication_hashes(app)
    if any(
        identity in protected for identity in
        (result_sha256, sha256, *(item["sha256"] for item in profiles))
    ):
        raise PermissionError("publication_source_withheld")
    try:
        raw = read_matrix_plot_blob(app.workspace, sha256)
    except (ValueError, FileNotFoundError) as error:
        raise OperationConflict(
            "Matrix diagnostic artifact differs from its receipt"
        ) from error
    return raw, ARTIFACT_MEDIA[name], name, result_sha256


def _record_event(ledger, lineage_id, event, state):
    authority = ledger.scientific
    with authority._execution_tx(lineage_id) as db:
        authority._append_execution_record(
            db,
            lineage_id,
            {
                "kind": "execution_event",
                "record_id": f"matrix-audit-{state['audit_id']}-{event}",
                "event": event,
                "state": state,
                "state_sha256": digest(state),
            },
        )


def _column(columns, supplied, default, role):
    if supplied is not None:
        if supplied not in columns:
            raise ValueError(
                f"The declared {role} column is absent from aligned metadata"
            )
        return supplied
    return default if default in columns else None


def _snapshot_sources(record, directory, maximum_bytes):
    profiles = record.get("source_profiles") or [record["profile"]]
    if sum(profile["size_bytes"] for profile in profiles) > maximum_bytes:
        raise ValueError("Matrix audit input set exceeds the bounded staging budget")
    groups, staged = {}, []
    for profile in profiles:
        source = Path(profile["source"])
        folder = groups.setdefault(source.parent, directory / str(len(groups)))
        folder.mkdir(mode=0o700, exist_ok=True)
        destination = folder / source.name
        copied = copy_artifact(
            source,
            destination,
            max_bytes=profile["size_bytes"],
            max_entries=1,
        )
        if (
            copied["kind"] != "file"
            or copied["size_bytes"] != profile["size_bytes"]
            or copied["sha256"] != profile["sha256"]
        ):
            raise OperationConflict(
                "Matrix audit source bytes changed during snapshot capture"
            )
        destination.chmod(0o400)
        staged.append({**profile, "source": str(destination), "path": str(destination)})
    if record.get("sources"):
        return {
            **record,
            "sources": [profile["source"] for profile in staged],
            "source_profiles": staged,
        }
    return {**record, "source": staged[0]["source"], "profile": staged[0]}


def _write_aligned_metadata(intake, path, columns):
    samples = intake["sample_ids"]
    label = intake["observation_column"]
    metadata = (
        {row[label]: row for row in intake["metadata_rows"]}
        if label is not None
        else {}
    )
    if any(
        not metadata.get(sample, {}).get(name) for sample in samples for name in columns
    ):
        raise ValueError(
            "The selected batch and condition columns require nonempty aligned labels"
        )
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["sample_id", *columns])
        writer.writerows(
            [sample, *(metadata[sample][name] for name in columns)]
            for sample in samples
        )


def _batch_report(dispatcher, arguments, *, batch_col, condition_col, quality_status):
    if batch_col is None:
        return None
    report = {
        "column": batch_col,
        "adjusted_for": condition_col,
        "status": "unavailable",
    }
    if quality_status == "fail":
        return report
    try:
        batch = json.loads(dispatcher.execute("analyze_batch_effects", {
            **arguments, "batch_col": batch_col, "condition_col": condition_col,
        }))
    except (ValueError, ArithmeticError) as error:
        report["error_type"] = type(error).__name__
        return report
    report.update(
        status="descriptive" if batch["identifiable"] else "not_identifiable",
        genes=[{"gene_id": gene, **row} for gene, row in batch["genes"].items()],
        design=batch["design"],
        transformation=batch["transformation"],
        pca=batch["pca"],
        diagnostics=batch["diagnostics"],
        gene_scope=batch["gene_scope"],
    )
    return report


def _run_tools(intake, workspace, batch_col, condition_col):
    samples = intake["sample_ids"]
    columns = [name for name in (batch_col, condition_col) if name is not None]
    measurement = intake["measurement"]
    dispatcher = ToolDispatcher()
    path = workspace / "observations.csv"
    _write_aligned_metadata(intake, path, columns)
    arguments = {
        "counts_path": str(intake["expression_path"]),
        "metadata_path": str(path),
        "time_col": None,
        "data_type": "counts" if measurement in COUNT_MEASUREMENTS else "continuous",
        "layer": intake["layer"],
    }
    with capture_tool_events() as events:
        qc = json.loads(dispatcher.execute("audit_expression_quality", arguments))
        metrics = qc["metrics"]
        values = metrics.get("nonzero_per_sample")
        if not isinstance(values, dict) or set(values) != set(samples):
            raise ValueError(
                "Expression audit did not return counts for the exact sample axis"
            )
        batch_report = _batch_report(
            dispatcher, arguments, batch_col=batch_col, condition_col=condition_col,
            quality_status=qc["status"],
        )
    return {
        "definition": "finite_nonzero_entries_on_the_selected_measurement_layer",
        "measurement": measurement,
        "layer": intake["layer"] or "stored_default",
        "sample_count": metrics["sample_count"],
        "gene_count": metrics["gene_count"],
        "nonzero_per_sample": [
            {"sample_id": sample, "nonzero_genes": values[sample]} for sample in samples
        ],
        "zero_expression_samples": metrics["zero_expression_samples"],
        "quality_status": qc["status"],
        "quality_metrics": {
            key: metrics[key] for key in (
                "zero_fraction", "missing_value_count", "nonfinite_value_count",
                "negative_value_count", "library_size_summary",
            )
        },
        "quality_diagnostics": qc["diagnostics"],
        "batch": batch_report,
        "tool_receipts": [
            {
                "tool": event["tool_name"],
                "event_sha256": digest(event),
                "succeeded": event["succeeded"],
            }
            for event in events
        ],
    }


def _audit_columns(record, intake, intents):
    if not (_PCA_INTENTS | _BATCH_INTENTS).intersection(intents):
        return None, None
    columns = intake["metadata_columns"]
    context = record["scientific_context"]
    # An unqualified PCA request needs a grouping choice for this registered tool.
    default_batch = "batch" if _BATCH_INTENTS.intersection(intents) else None
    batch_col = _column(columns, context.get("batch_col"), default_batch, "batch")
    identifiers = {"sample_id", intake["observation_column"]}
    condition_col = _column(
        columns, context.get("condition_col"), "condition", "condition"
    )
    if batch_col is None:
        reason = "Choose the observed batch column for the registered diagnostic."
        excluded = identifiers | {condition_col}
        options = [name for name in columns if name not in excluded]
        if not options:
            raise ValueError(
                "Provide aligned metadata with a batch column."
            )
        raise MatrixIntakeNeedsInformation(
            reason,
            [
                {
                    "field": "batch_col",
                    "scope": "scientific_context",
                    "type": "select",
                    "label": "Batch column",
                    "required": True,
                    "reason": reason,
                    "options": [{"value": name, "label": name} for name in options],
                }
            ],
        )
    if batch_col == condition_col or batch_col in identifiers:
        raise ValueError(
            "Batch, adjustment and profile identity require distinct columns"
        )
    if condition_col is not None and condition_col in identifiers:
        raise ValueError("The profile identity column cannot be an adjustment factor")
    return batch_col, condition_col


def matrix_audit_incomplete(audit):
    batch = audit["batch"]
    return batch is not None and (
        batch["status"] == "unavailable"
        or (
            _PCA_INTENTS.intersection(audit.get("requested_intents", ()))
            and batch.get("pca") is None
        )
    )


def matrix_audit_summary(audit):
    message = (
        f"Counted finite nonzero entries across {audit['gene_count']} genes "
        f"for {audit['sample_count']} observed profiles."
    )
    batch = audit["batch"]
    if batch is None:
        return message
    genes = batch.get("genes", [])
    estimated = sum(gene["batch_partial_r2"] is not None for gene in genes)
    status = {
        "descriptive": (
            f"Batch partial R² available for {estimated} of {len(genes)} genes."
        ),
        "not_identifiable": "The design could not identify batch partial R².",
        "unavailable": "The batch diagnostic did not return estimates.",
    }[batch["status"]]
    pca = batch.get("pca")
    pca_summary = (
        f"PCA retained scores for {pca['sample_count']} "
        f"of {pca['source_sample_count']} profiles."
        if pca is not None
        else "PCA scores are unavailable."
    )
    return f"{message} {status} {pca_summary}"


def _batch_tables(batch):
    if batch is None or "genes" not in batch:
        return []
    rows = [
        [
            row["gene_id"],
            row["batch_partial_r2"],
            row["full_r2"],
            row["residual_df"],
            row["status"],
        ]
        for row in batch["genes"]
    ]
    columns = ["Gene", "Batch partial R²", "Full model R²", "Residual df", "Status"]
    if batch["adjusted_for"] is not None:
        columns.insert(2, "Condition partial R²")
        for values, gene in zip(rows, batch["genes"], strict=True):
            values.insert(2, gene.get("condition_partial_r2"))
    return [{
        "caption": "Per-gene batch diagnostics", "columns": columns, "rows": rows,
        "denominator": len(rows), "missing_count": sum(row[1] is None for row in rows),
    }]


def _pca_table(pca):
    axes = [f"PC{index}" for index in range(1, len(pca["variance_ratio"]) + 1)]
    return {
        "caption": "PCA scores by batch",
        "columns": ["Profile", "Batch", *axes],
        "rows": [
            [sample, batch, *scores]
            for sample, batch, scores in zip(
                pca["sample_ids"], pca["batch_labels"], pca["coordinates"], strict=True
            )
        ],
        "denominator": pca["sample_count"], "missing_count": 0,
    }


def _visible_metrics(audit, batch):
    metrics = {
        "Observed profiles": audit["sample_count"],
        "Genes": audit["gene_count"],
        "Profiles with no finite nonzero entries": audit["zero_expression_samples"],
        "Measurement": audit["measurement"] or "Undeclared in retained audit",
        "Layer": audit["layer"],
        "Expression quality status": audit["quality_status"],
    }
    quality = audit.get("quality_metrics") or {}
    if "zero_fraction" in quality:
        metrics["Zero entries"] = f"{quality['zero_fraction']:.1%}"
        metrics["Nonfinite entries"] = quality["nonfinite_value_count"]
    if batch is not None:
        metrics.update({"Batch column": batch["column"],
                        "Condition adjustment": batch["adjusted_for"] or "None"})
    if batch and batch.get("design"):
        design = batch["design"]
        metrics["Batch design rank"] = f"{design['rank']} of {design['num_parameters']}"
        metrics["Genes in batch diagnostic"] = len(batch["genes"])
        metrics["Batch diagnostic scale"] = {
            "log1p_RLE_normalized_counts": "RLE-normalized counts with log1p",
            "none_continuous_input": (
                "Stored values with undeclared scale" if audit["measurement"] is None
                else "Stored continuous expression"
            ),
        }.get(batch["transformation"], batch["transformation"])
    return [{"label": label, "value": value} for label, value in metrics.items()]


def matrix_audit_projection(audit):
    """Project recorded results after the current disclosure receipt is checked."""
    batch = audit["batch"]
    pca = (batch or {}).get("pca")
    tables = [
        {
            "caption": "Finite nonzero genes per observed profile",
            "columns": ["Profile", "Nonzero genes"],
            "rows": [
                [row["sample_id"], row["nonzero_genes"]]
                for row in audit["nonzero_per_sample"]
            ],
            "denominator": audit["sample_count"],
            "missing_count": 0,
        },
        *_batch_tables(batch),
    ]
    limitations = [
        "Nonzero counts describe finite entries on the stored measurement layer."
    ]
    diagnostics = [*audit["quality_diagnostics"], *(batch or {}).get("diagnostics", [])]
    limitations.extend(
        list(dict.fromkeys(row["message"] for row in diagnostics if row.get("message")))
    )
    if batch is not None:
        limitations.append(
            "Batch associations are descriptive. Biological independence and "
            "causality remain unverified."
        )
        if batch["adjusted_for"] is not None and pca is not None:
            limitations.append("Condition adjustment applies to the per-gene model.")
    if audit["measurement"] is None:
        limitations.append(
            "This retained audit ran without a declared measurement scale."
        )
    plot = None
    if pca is not None:
        plot = {
            "kind": "batch_pca",
            "table_index": len(tables),
            "variance_ratio": pca["variance_ratio"],
            "source_sample_count": pca["source_sample_count"],
            "gene_count": len(pca["gene_ids"]),
            "sampling": pca["sampling"],
        }
        tables.append(_pca_table(pca))
    projection = {
        "matrix_metrics": _visible_metrics(audit, batch),
        "limitations": limitations, "tables": tables, "plot": plot,
    }
    if "plot_artifacts" in audit:
        projection["plot_artifacts"] = audit["plot_artifacts"]
    return projection


def run_matrix_audit(
    record, *, intents, ledger, workspace, verify_sources, maximum_bytes
):
    lineage = record["adaptive_lineage"]
    lineage_id = lineage["lineage_id"]
    broker = EvidenceBroker.open_existing(ledger.scientific)
    broker.check_delivery(
        lineage["context_id"],
        lineage["source_id"],
        lineage_id=lineage_id,
        role="scientist",
    )
    verify_sources()
    state = {
        "audit_id": uuid.uuid4().hex,
        "intake_sha256": lineage["intake_sha256"],
        "intake_exposure_sha256": lineage["exposure_basis_sha256"],
    }
    _record_event(ledger, lineage_id, "matrix_audit_started", state)
    try:
        with tempfile.TemporaryDirectory(
            prefix=".matrix-inputs-", dir=workspace
        ) as folder:
            directory = Path(folder)
            staged = _snapshot_sources(record, directory, MAX_SOURCE_BYTES)
            intake = inspect_matrix_intake(staged)
            if intake["measurement"] is None:
                verify_sources()
                raise MatrixAuditNeedsMeasurement(intake, record)
            batch_col, condition_col = _audit_columns(record, intake, intents)
            packet = {
                "format": AUDIT_FORMAT,
                **state,
                "input_sha256": lineage["intake"]["source_sha256"],
                "intake_fact_provenance": intake["fact_provenance"],
                "requested_intents": list(intents),
                **_run_tools(intake, directory, batch_col, condition_col),
            }
        packet["plot_artifacts"] = retain_matrix_audit_plot(
            workspace, record["id"], state["audit_id"], packet["batch"],
        )
        verify_sources()
        if len(json.dumps(packet, indent=2, allow_nan=False).encode()) > maximum_bytes:
            raise ValueError("Descriptive result exceeds the Workbench report bound")
        roots = ledger._read(
            lambda db: tuple(broker._source(db, lineage["source_id"])["roots"]),
        )
        policy = AccessPolicy(
            "matrix-audit-" + digest(roots)[:32],
            1,
            allowed_roots=roots,
            diagnostic_policy_id=digest({"registered_diagnostic": AUDIT_FORMAT}),
        )
        policy_sha = broker.register_policy(policy)
        context_id = f"matrix-audit-{lineage_id}-{policy_sha[:16]}"
        broker.register_context(
            context_id,
            principal_id="investigation-service",
            lineage_id=lineage_id,
            role="scientist",
            phase="readback",
            policy_sha256=policy_sha,
            parents=(lineage["context_id"],),
        )
        result_sha = digest(packet)
        source_id = broker.register_source(
            f"matrix-audit-{result_sha}",
            content_sha256=result_sha,
            roots=roots,
            parents=(lineage["source_id"],),
            classification="diagnostic",
        )
        reservation = broker.reserve(context_id, source_id, packet)
        broker.mark_attempted(reservation["reservation_id"])
        packet.update(
            evidence_context_id=context_id,
            evidence_source_id=source_id,
            disclosure_reservation_id=reservation["reservation_id"],
            exposure_basis_sha256=digest(broker.exposure(context_id, source_id)),
        )
        _record_event(
            ledger,
            lineage_id,
            "matrix_audit_completed",
            {
                **state,
                "result_sha256": result_sha,
                "exposure_basis_sha256": packet["exposure_basis_sha256"],
                "disclosure_reservation_id": reservation["reservation_id"],
            },
        )
        return packet
    except Exception as error:
        _record_event(
            ledger,
            lineage_id,
            "matrix_audit_failed",
            {
                **state,
                "error_type": type(error).__name__,
            },
        )
        raise
