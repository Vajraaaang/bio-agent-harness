"""Separate, bounded public-task reporting; missing runs are never successes."""
from collections import Counter
import hashlib
import html
from pathlib import Path

from .contracts import CONDITIONS, canonical_json_bytes
from .evidence import _read, decode_json
from .public_study import DATASETS, schedule_public_attempts


def load_public_records(path, plan, *, execution_receipt_sha256=None, allow_result_analysis=False):
    if allow_result_analysis is not True:
        raise PermissionError("Public result reading is disabled")
    from .biology_reporting import _path, _digest
    from .public_records import parse_public_attempt
    from .evidence import verify_receipts
    path = _path(path)
    raw = _read(path, 32_000_000)
    envelope = decode_json(raw)
    manifest_raw = _read(path.parent / "execution.json", 16_000_000)
    manifest_hash = hashlib.sha256(manifest_raw).hexdigest()
    if execution_receipt_sha256 is not None and _digest(execution_receipt_sha256) != manifest_hash:
        raise ValueError("Public execution differs from the independently retained receipt")
    manifest = decode_json(manifest_raw)
    if (envelope.get("record_format") != "devbench.public.attempt-records/1"
            or envelope.get("plan_identity") != plan.sha256()
            or manifest.get("record_format") != "devbench.public.execution/1"
            or manifest.get("plan_identity") != plan.sha256()
            or manifest.get("aggregate_path") != str(path)
            or manifest.get("aggregate_sha256") != hashlib.sha256(raw).hexdigest()):
        raise ValueError("Public record envelope or aggregate binding is invalid")
    scheduled = {row["attempt_id"]: row for row in schedule_public_attempts(plan)}
    if not isinstance(envelope.get("records"), list) or len(envelope["records"]) != len(scheduled):
        raise ValueError("All planned public slots, including failures, must be retained")
    records = [parse_public_attempt(row) for row in envelope["records"]]
    if len({row.attempt_id for row in records}) != len(records) or {row.attempt_id for row in records} != set(scheduled):
        raise ValueError("Public attempt identities do not match the complete schedule")
    artifacts = manifest.get("attempt_artifacts", {})
    if set(artifacts) != set(scheduled):
        raise ValueError("Public artifact inventory differs from the schedule")
    total_bytes = 0
    verified_tools = 0
    eligibility = manifest.get("reference_eligibility", {})
    if not isinstance(eligibility, dict) or set(eligibility) - set(plan.datasets):
        raise ValueError("Public reference eligibility inventory is invalid")
    dataset_bindings = {}
    for row in records:
        slot = scheduled[row.attempt_id]
        if any(getattr(row, key) != value for key, value in slot.items()) or row.source_id != DATASETS[row.dataset_id][0]:
            raise ValueError("Public record differs from its scheduled slot")
        declared = eligibility.get(row.dataset_id, {})
        if (row.reference_task_eligible is not declared.get("reference_task_eligible")
                or (declared and declared.get("reference_sha256") != row.reference_sha256)):
            raise ValueError("Public reference eligibility differs from its retained dataset binding")
        binding = tuple(getattr(row, field) for field in ("input_sha256", "reference_sha256", "preparation_sha256", "independence_id"))
        if dataset_bindings.setdefault(row.dataset_id, binding) != binding:
            raise ValueError("Public arms do not share the same prepared dataset")
        artifact = artifacts[row.attempt_id]
        attempt_dir = path.parent / "attempts" / row.attempt_id
        loaded = {}
        for key, filename in (("record", "record.json"), ("execution_detail", "execution.json"),
                              ("retained_tool_root", "retained_tool_root.json")):
            asset = artifact.get(key + "_path")
            digest = artifact.get(key + "_sha256")
            if asset is None and digest is None and key == "retained_tool_root":
                continue
            if _path(asset) != attempt_dir / filename:
                raise ValueError("Public artifact path escapes its assigned location")
            data = _read(Path(asset), min(16_000_000, 64_000_000 - total_bytes))
            total_bytes += len(data)
            if hashlib.sha256(data).hexdigest() != _digest(digest):
                raise ValueError("Public attempt artifact differs from its receipt")
            loaded[key] = decode_json(data)
        if parse_public_attempt(loaded["record"]) != row:
            raise ValueError("Public per-attempt record differs from the aggregate")
        if loaded["execution_detail"].get("attempt_id") != row.attempt_id:
            raise ValueError("Public execution detail belongs to another attempt")
        if "retained_tool_root" in loaded and row.trusted_tool_capture_complete and row.condition != "baseline":
            receipt = loaded["retained_tool_root"]
            evidence = verify_receipts(attempt_dir / "receipts", expected_receipt=receipt,
                expected_input_sha256=row.input_sha256, expected_implementation_sha256=manifest["source_sha256"],
                allow_evidence_read=True)
            if evidence.condition != row.condition or len(evidence.events) != row.trusted_receipt_count:
                raise ValueError("Public tool receipts differ from recorded activity")
            verified_tools += 1
        elif row.trusted_tool_capture_complete and row.condition != "baseline":
            raise ValueError("Claimed public tool capture has no retained root")
    if manifest.get("planned_attempt_count") != len(scheduled) or manifest.get("attempted_count") != sum(row.attempted for row in records):
        raise ValueError("Public execution totals differ from retained attempts")
    return records, {"execution_manifest_sha256": manifest_hash,
                     "receipt_scope": "separately_retained_root" if execution_receipt_sha256 else "internal_consistency_only",
                     "verified_tool_sessions": verified_tools}


def summarize_public(plan, records=None, *, allow_result_analysis=False):
    if records is not None and allow_result_analysis is not True:
        raise PermissionError("Public result analysis is disabled")
    from .public_records import parse_public_attempt
    measured = records is not None
    records = [] if records is None else [parse_public_attempt(row) for row in records]
    if measured:
        slots = {row["attempt_id"]: row for row in schedule_public_attempts(plan)}
        if len(records) != len(slots) or {row.attempt_id for row in records} != set(slots):
            raise ValueError("Public summaries require the complete planned attempt inventory")
        for row in records:
            if any(getattr(row, key) != value for key, value in slots[row.attempt_id].items()):
                raise ValueError("Public summary record belongs to another schedule")
    cells = []
    for dataset in plan.datasets:
        for condition in CONDITIONS:
            rows = [row for row in records if row.dataset_id == dataset and row.condition == condition]
            eligible = [row for row in rows if row.reference_task_eligible is True]
            successes = sum(row.reference_task_success is True for row in eligible)
            accepted = sum(row.reference_task_success is True and row.report_accepted is True for row in eligible)
            from .execution_usage import tool_usage_metrics
            cells.append({**tool_usage_metrics(rows), "dataset_id": dataset, "source_id": DATASETS[dataset][0], "condition": condition,
                "planned_attempts": plan.repetitions, "attempted": sum(row.attempted for row in rows),
                "completed": sum(row.completion_status == "completed" for row in rows),
                "eligible_planned_slots": len(eligible), "eligibility_unknown_slots": plan.repetitions-sum(row.reference_task_eligible is not None for row in rows),
                "reference_task_success_rate": successes/len(eligible) if eligible else None,
                "accepted_reference_success_rate": accepted/len(eligible) if eligible else None,
                "complete_biological_correctness": None,
                "observed_models": sorted({row.actual_model for row in rows if row.model_identity_verified}),
                "model_identity_unknown": sum(not row.model_identity_verified for row in rows),
                "failures": dict(Counter(error for row in rows for error in (row.errors or (() if row.completion_status == "completed" else (row.completion_status,))))),
                "tool_calls": sum(row.trusted_tool_calls for row in rows) if rows and all(row.trusted_tool_calls is not None for row in rows) else None,
                "tokens": sum(row.tokens for row in rows) if rows and all(row.tokens is not None for row in rows) else None,
                "peak_memory_mb": max(row.RSS_MB for row in rows) if rows and all(row.RSS_MB is not None for row in rows) else None,
                "total_elapsed_sec": sum(row.elapsed_sec for row in rows) if rows and all(row.elapsed_sec is not None for row in rows) else None})
    return {"record_format": "devbench.public-report/1", "study_id": plan.study_id, "plan_identity": plan.sha256(),
            "source_kind": "public_processed", "status": "measured_records" if records else "planned_not_run",
            "cells": cells, "attempts": [row.model_dump(mode="json") for row in records],
            "calibration_status": plan.calibration_status, "pool_with_synthetic_results": False,
            "clinical_qualification": False, "complete_biological_correctness": None,
            "limitations": ["Success concerns only the declared public reference task; no complete biological ground truth.",
                            "The developmental panels share specimens and are not independent cohorts; animal identity is unverified.",
                            "SEQC uses technical libraries of shared RNA materials, not independent patients.",
                            "ERCC reference success is control-specific and does not validate normalization of endogenous RNA.",
                            "Enforced accepted results are constrained to recorded tool outputs; raw reference correctness remains a separate outcome.",
                            "Thresholds are provisional. No confidence interval or general harness benefit follows from this small track."]}


def write_public_report(plan, output, records=None, *, storage=None, allow_result_analysis=False):
    summary = summarize_public(plan, records, allow_result_analysis=allow_result_analysis)
    summary.update(storage or {})
    from .permissions import require_protected_storage
    output = Path(output)
    if any(path.is_symlink() for path in (output, *output.parents)):
        raise ValueError("Public report output must not contain symlinks")
    require_protected_storage(output)
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    def show(value):
        return "unknown" if value is None else (f"{100*value:.1f}%" if isinstance(value, float) else str(value))
    lines = ["# Public reference tasks", "", f"Status: {summary['status']}. Synthetic results are reported separately.", "",
             "| Dataset | Condition | Attempted / planned | Eligibility unknown | Reference success | Accepted reference success | Tool calls |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for cell in summary["cells"]:
        lines.append(f"| {cell['dataset_id']} | {cell['condition']} | {cell['attempted']} / {cell['planned_attempts']} | "
                     f"{cell['eligibility_unknown_slots']} | {show(cell['reference_task_success_rate'])} | {show(cell['accepted_reference_success_rate'])} | {show(cell['tool_calls'])} |")
    lines += ["", *summary["limitations"], "", "Missing planned eligible attempts remain in the denominator. Unknown eligibility is disclosed."]
    markdown = "\n".join(lines)
    raw = canonical_json_bytes(summary)
    (output / "report.json").write_bytes(raw)
    (output / "report.md").write_text(markdown)
    table = "".join("<tr>" + "".join("<td>" + html.escape(str(value)) + "</td>" for value in (
        cell["dataset_id"], cell["condition"], f"{cell['attempted']} / {cell['planned_attempts']}",
        cell["eligibility_unknown_slots"],
        show(cell["reference_task_success_rate"]), show(cell["accepted_reference_success_rate"]), show(cell["tool_calls"])))
        + "</tr>" for cell in summary["cells"])
    (output / "index.html").write_text("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
        "<title>DevBench public reference tasks</title><style>body{font:16px system-ui;color:#183042;background:#f6f8fa;max-width:1150px;margin:40px auto;padding:24px}"
        "h1{font-size:2.2rem;margin-bottom:12px}p,li{line-height:1.6}table{border-collapse:collapse;width:100%;background:white}"
        "th,td{text-align:left;padding:14px;border-bottom:1px solid #dbe3e9}th{background:#e7eef3}.table{overflow-x:auto}"
        "a{color:#135b83}.status{display:inline-block;padding:6px 12px;background:#e0eef5;border-radius:5px}</style></head><body>"
        "<p>DEVBENCH · PUBLIC DATA</p><h1>Limited reference tasks</h1><p class='status'>" + html.escape(summary["status"])
        + "</p><p>Developmental observations and SEQC controls. Synthetic recovery scores are reported separately.</p>"
        "<div class='table'><table><thead><tr><th scope='col'>Dataset</th><th scope='col'>Condition</th><th scope='col'>Attempted / planned</th>"
        "<th scope='col'>Eligibility unknown</th><th scope='col'>Reference success</th><th scope='col'>Accepted reference success</th><th scope='col'>Tool calls</th></tr></thead><tbody>"
        + table + "</tbody></table></div><p>Unknown means unavailable, not zero. Missing planned eligible attempts remain in the denominator.</p>"
        "<h2>Interpretation limits</h2><ul>" + "".join("<li>" + html.escape(item) + "</li>" for item in summary["limitations"])
        + "</ul><p><a href='report.md'>Markdown report</a> · <a href='report.json'>Structured report</a></p></body></html>")
    return {"report": str(output / "index.html"), "report_sha256": hashlib.sha256(raw).hexdigest(), "status": summary["status"]}
