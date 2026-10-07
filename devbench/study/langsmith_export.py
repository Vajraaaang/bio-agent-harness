"""Optional, explicit post-run LangSmith export. No participant or grader imports."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import stat
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import NAMESPACE_URL, UUID, uuid5

from .contracts import canonical_json_bytes
from .evidence import decode_json


FORMAT = "devbench.langsmith-export/1"
CONDITIONS = ("baseline", "optional", "enforced")
FORMATS = {"devbench.study.execution/1": "classic", "devbench.public.execution/1": "public",
           "devbench.biology.execution/1": "biology", "devbench.preflight-only/1": "preflight"}
TERMINAL = {"finished", "finished_with_blocked_attempts", "blocked", "interrupted", "failed", "verified"}
ENDPOINTS = {"https://api.smith.langchain.com", "https://eu.api.smith.langchain.com",
             "https://aws.api.smith.langchain.com", "https://apac.api.smith.langchain.com"}
TOOLS = frozenset("audit_quality validate_design fit_family fit_trajectory inspect_study_design "
    "summarize_observed_expression analyze_batch_effects compact_manifold audit_expression_quality "
    "validate_design_matrix compact_temporal_state fit_temporal_trajectory infer_lineage "
    "infer_regulatory_network regulon_enrichment motif_enrichment calculate_temporal_monotonicity "
    "audit_biological_units fit_bulk_contrast fit_pseudobulk assess_normalization "
    "command_execution file_change temporal_event_analysis periodic_trajectory_analysis condition_time_analysis "
    "dose_response_analysis mean_condition_analysis differential_variability_analysis validate_biological_units "
    "covariate_response_analysis normalization_scale_analysis regulatory_evidence_audit bulk_mixture_deconvolution "
    "observation_robustness_analysis descriptive_expression_summary model_adequacy_analysis "
    "pseudobulk_membership_audit cell_type_state_analysis cell_composition_analysis lineage_history_analysis "
    "competing_program_analysis single_cell_quality_audit single_cell_contamination_analysis "
    "perturbation_design_audit perturbation_response_analysis audit_public_inputs "
    "summarize_developmental_observations normalize_seqc_libraries".split())
EVENTS = {"tool_use", "tool_result", "self_correction", "error", "permission_denied", "response_summary", "session_started"}
STATUSES = TERMINAL | {"completed", "not_started", "not_run", "timeout", "provider_error", "tool_error",
                      "invalid_output", "succeeded", "unavailable", "running", "unknown"}
ANSWER_KEYS = frozenset("analysis status claim_scope stage_kinetics master_regulators condition_number_validated "
    "biological_rationale gene_statistics gene_id estimate effect_size standard_error p_value q_value "
    "confidence_interval lower upper score metrics biological_metrics precision recall f1 rmse mae r2 "
    "value passed reason diagnostics diagnostic_outcomes refusal_outcomes reference_task_success".split())
OUTCOMES = ("attempted", "accepted_final", "scientific_success", "reference_task_success", "report_accepted",
            "available_target_scientific_success", "workflow_passed", "trusted_tool_capture_complete", "trusted_tool_calls",
            "elapsed_sec", "RSS_MB", "tokens", "cost")
BOOLEAN_OUTCOMES = frozenset(OUTCOMES) - {"trusted_tool_calls", "elapsed_sec", "RSS_MB", "tokens", "cost"}
MAX_FILE = 8_000_000
MAX_TOTAL = 64_000_000
MAX_SPANS = 10_000


def _hash(value):
    return hashlib.sha256(value if isinstance(value, bytes) else canonical_json_bytes(value)).hexdigest()


def _absolute(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Use absolute paths without traversal or symlink components")
    return path


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def _status(value):
    return value if isinstance(value, str) and value in STATUSES else "unknown"


def _model(value):
    return value if isinstance(value, str) and re.fullmatch(r"(?:gpt-|codex-|o[1-9])[A-Za-z0-9._-]{1,80}", value) else None


def _time(value):
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def _redact(value, depth=0):
    """Keep bounded numeric structure; no input-derived free text or identifiers."""
    if depth > 12:
        raise ValueError("Redacted structure exceeds nesting limit")
    if value is None or type(value) is bool:
        return value
    if type(value) in (int, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return {"redacted_text_sha256": _hash(value)}
    if isinstance(value, list):
        if len(value) > 1000:
            return {"omitted_items": len(value), "content_sha256": _hash(value)}
        return [_redact(item, depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > 1000:
            return {"omitted_fields": len(value), "content_sha256": _hash(value)}
        return {key if key in ANSWER_KEYS else "field_" + _hash(key): _redact(item, depth + 1)
                for key, item in value.items()}
    raise ValueError("Unsupported evidence value")


def _objects(value):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_SPANS or not all(isinstance(v, dict) for v in value):
        raise ValueError("Expected a bounded list of evidence objects")
    return value


class _Evidence:
    def __init__(self, root):
        self.root = _absolute(root)
        self.files = {}
        self.total = 0

    def read(self, path, *, optional=False, lines=False):
        path = _absolute(path)
        if optional and not path.exists():
            return None
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE:
                raise ValueError("Evidence must be a bounded regular file")
            raw = stream.read(MAX_FILE + 1)
        self.total += len(raw)
        if len(raw) > MAX_FILE or self.total > MAX_TOTAL:
            raise ValueError("Evidence exceeds export byte budget")
        self.files[str(path)] = {"sha256": _hash(raw), "bytes": len(raw), "mtime_ns": info.st_mtime_ns}
        if lines:
            if raw and not raw.endswith(b"\n"):
                raise ValueError("Incomplete JSONL evidence")
            values = [decode_json(row) for row in raw.splitlines() if row.strip()]
            if not all(isinstance(row, dict) for row in values):
                raise ValueError("JSONL records must be objects")
            return values
        return decode_json(raw)

    def digest(self, path):
        return self.files[str(path)]["sha256"]

    def unchanged(self):
        for name, expected in self.files.items():
            path = _absolute(name)
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected["sha256"]:
                    raise ValueError("Source evidence changed during export")


def _write_new(path, value):
    raw = canonical_json_bytes(value) + b"\n"
    descriptor = os.open(_absolute(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
    return _hash(raw)


def _command_name(value):
    try:
        tokens = shlex.split(value) if isinstance(value, str) else value if isinstance(value, list) else []
        first = Path(tokens[0]).name
    except (ValueError, TypeError, IndexError):
        return "unparsed"
    return first if first in {"python", "python3", "Rscript", "bash", "sh", "zsh", "ls", "cat", "head", "tail", "wc", "rg", "grep", "git", "sed", "awk"} else "redacted"


def prepare_export(run_dir, output_dir, *, data_scope, project="devbench", workspace_root=None,
                   max_spans=2000, expected_manifest_sha256=None, allow_evidence_read=False):
    """Prepare a deterministic payload locally. This function has no network path."""
    if allow_evidence_read is not True:
        raise PermissionError("LangSmith preview requires explicit evidence-read authorization")
    if data_scope not in {"synthetic", "public_processed"}:
        raise ValueError("Only synthetic or public_processed evidence is supported")
    if type(max_spans) is not int or not 1 <= max_spans <= MAX_SPANS:
        raise ValueError("Invalid span budget")
    if not isinstance(project, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", project) is None:
        raise ValueError("Project must be a bounded identifier")
    root, output = _absolute(run_dir), _absolute(output_dir)
    workspace_root = _absolute(workspace_root) if workspace_root is not None else None
    if output.exists() or output.is_relative_to(root) or root.is_relative_to(output):
        raise ValueError("Export output must be fresh and disjoint from run evidence")
    if workspace_root and (output.is_relative_to(workspace_root) or workspace_root.is_relative_to(output)):
        raise ValueError("Export output must be disjoint from participant workspaces")
    reader = _Evidence(root)
    manifests = [root / name for name in ("execution.json", "preflight.json") if (root / name).exists()]
    if len(manifests) != 1:
        raise ValueError("Expected one current execution or standalone-preflight manifest")
    manifest_path = manifests[0]
    manifest = reader.read(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("record_format") not in FORMATS:
        raise ValueError("Unsupported run manifest format")
    if manifest.get("status") not in TERMINAL:
        raise ValueError("Only terminal runs may be exported after measurement")
    manifest_hash = reader.digest(manifest_path)
    if expected_manifest_sha256 is not None and manifest_hash != expected_manifest_sha256:
        raise ValueError("Manifest differs from supplied root hash")
    track = FORMATS[manifest["record_format"]]
    preflight = track == "preflight"
    if preflight:
        track = manifest.get("track")
        if track not in {"classic", "biology", "public"}:
            raise ValueError("Unsupported standalone probe track")
        mapping = manifest.get("probes")
        selected = manifest.get("requested_conditions", list(CONDITIONS))
        if (not isinstance(selected, list) or not selected or len(set(selected)) != len(selected)
                or any(c not in CONDITIONS for c in selected) or not isinstance(mapping, dict) or set(mapping) != set(selected)):
            raise ValueError("Standalone probes must retain every requested condition")
        rows = [dict(mapping[c], condition=c, attempt_id=c) for c in CONDITIONS if c in selected]
    else:
        records_path = root / ("attempt_records.json" if track == "classic" else "records.json")
        aggregate = reader.read(records_path)
        expected = manifest.get("attempt_records_sha256" if track == "classic" else "aggregate_sha256")
        if reader.digest(records_path) != expected:
            raise ValueError("Attempt records differ from manifest hash")
        rows = aggregate if isinstance(aggregate, list) else aggregate.get("records")
    if not isinstance(rows, list) or not rows or len(rows) > max_spans:
        raise ValueError("Missing records or exceeded span budget")
    if track == "public" and data_scope != "public_processed":
        raise ValueError("Public-track evidence requires public_processed scope")
    if track == "biology" and data_scope != "synthetic":
        raise ValueError("Biology benchmark evidence requires synthetic scope")
    runs, references, seen = [], {}, set()
    anchor = datetime.fromtimestamp(manifest_path.stat().st_mtime, timezone.utc)
    for row in rows:
        if not isinstance(row, dict) or row.get("condition") not in CONDITIONS:
            raise ValueError("Invalid condition record")
        attempt_id = row.get("attempt_id")
        if not isinstance(attempt_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,200}", attempt_id) is None or attempt_id in seen:
            raise ValueError("Invalid or duplicate attempt identifier")
        seen.add(attempt_id)
        if not preflight and row.get("source_kind", "synthetic" if track == "biology" else None) != data_scope:
            raise ValueError("Source scope mismatch; clinical or unclassified records are unsupported")
        directory = root / ("probes" if preflight else "attempts") / attempt_id
        details = reader.read(directory / "execution.json", optional=True) or {}
        if not isinstance(details, dict):
            raise ValueError("Invalid execution detail")
        detail_hash = (manifest.get("attempt_artifacts") or {}).get(attempt_id, {}).get("execution_detail_sha256")
        if detail_hash is not None and reader.files.get(str(directory / "execution.json"), {}).get("sha256") != detail_hash:
            raise ValueError("Execution detail differs from its manifest hash binding")
        adapter = details.get("adapter") or {}
        if not isinstance(adapter, dict):
            raise ValueError("Invalid adapter evidence")
        commands = _objects(adapter.get("commands"))
        observable = _objects(adapter.get("observable_events"))
        checkpoint = reader.read(directory / "dispatch_checkpoint.json", optional=True) or {}
        start = _time(checkpoint.get("timestamp"))
        timing = "coordinator_dispatch_plus_adapter_duration" if start is not None else "artifact_mtime_display_anchor_only"
        start = start or anchor
        elapsed = _number(adapter.get("duration_sec")) if timing.startswith("coordinator") else None
        end = start + timedelta(seconds=elapsed or 0)
        condition = row["condition"]
        parent_id = str(uuid5(NAMESPACE_URL, FORMAT + manifest_hash + attempt_id))
        metadata = {"condition": condition, "track": track, "data_scope": data_scope, "scored": not preflight,
            "attempt_key": _hash(attempt_id), "manifest_sha256": manifest_hash,
            "task_identity": row.get("task_identity", row.get("task_id")) if re.fullmatch(r"[a-f0-9]{64}", str(row.get("task_identity", row.get("task_id")))) else None,
            "input_sha256": row.get("input_sha256") if re.fullmatch(r"[a-f0-9]{64}", str(row.get("input_sha256"))) else None,
            "requested_model": _model(row.get("requested_model", adapter.get("requested_model"))),
            "actual_model": _model(row.get("actual_model")),
            "model_identity_verified": row.get("model_identity_verified") is True,
            "timing_basis": timing, "event_timing_available": False,
            "manifest_binding": "supplied_hash_matched" if expected_manifest_sha256 else "local_snapshot_only",
            "execution_detail_binding": "manifest_hash_matched" if detail_hash else "unbound_local_snapshot",
            "unknown_cli_event_count": sum(event.get("type") not in EVENTS for event in observable),
            "evidence_authority": "local_retained_records; not_regraded_by_exporter"}
        outputs = {}
        for key in OUTCOMES:
            value = row.get(key)
            if value is not None:
                if key in BOOLEAN_OUTCOMES and type(value) is not bool:
                    raise ValueError("Invalid binary benchmark outcome")
                if key not in BOOLEAN_OUTCOMES and (_number(value) is None
                        or (key in {"tokens", "trusted_tool_calls"} and type(value) is not int)):
                    raise ValueError("Invalid benchmark measurement")
            outputs[key] = value
        outputs.update(completion_status=_status(row.get("completion_status", row.get("status"))),
                       metrics=_redact(row.get("biological_metrics", row.get("metrics", {}))),
                       per_target_results=_redact(row.get("per_target_results", [])),
                       final_answer=None, final_answer_availability="not_loaded")
        if workspace_root is not None and not preflight and details.get("workspace"):
            workspace = _absolute(details["workspace"])
            if workspace == workspace_root or not workspace.is_relative_to(workspace_root):
                raise ValueError("Participant workspace is outside the explicitly allowed root")
            answer_path = workspace / "final_discovery.json"
            answer_hashes = [c.get("answer_sha256_at_completion") for c in commands
                            if c.get("succeeded") is True and c.get("direct_script_invocation") is True]
            expected_answer = answer_hashes[-1] if answer_hashes else None
            answer = reader.read(answer_path, optional=True) if expected_answer else None
            if answer is not None and reader.digest(answer_path) != expected_answer:
                raise ValueError("Final answer differs from the observed script-completion hash")
            if answer is not None:
                outputs.update(final_answer=_redact(answer), final_answer_availability="redacted_structure",
                               final_answer_sha256=reader.digest(answer_path))
                references[parent_id] = {"answer": str(answer_path)}
            elif not expected_answer:
                outputs["final_answer_availability"] = "unverified_answer_hash"
        parent = {"id": parent_id, "name": "devbench:" + condition, "run_type": "chain",
            "start_time": start.isoformat(), "end_time": end.isoformat(), "inputs": {}, "outputs": outputs,
            "session_name": project, "tags": ["devbench", condition, track, data_scope, "preflight" if preflight else "scored"],
            "extra": {"metadata": metadata}}
        runs.append(parent)
        references.setdefault(parent_id, {}).update(execution=str(directory / "execution.json"), record_key=attempt_id)

        def child(name, value, source, index, *, actual_start=None, duration=None, is_tool=False, error=False, parent=None):
            owner = parent or parent_id
            identity = str(uuid5(UUID(owner), name + str(index) + _hash(value)))
            child_start = actual_start or start
            child_end = child_start + timedelta(seconds=duration or 0)
            span = {"id": identity, "parent_run_id": owner, "name": name,
                "run_type": "tool" if is_tool else "chain", "start_time": child_start.isoformat(),
                "end_time": child_end.isoformat(), "inputs": {}, "outputs": value, "session_name": project,
                "tags": ["devbench", condition, track], "extra": {"metadata": {
                    "condition": condition, "scored": not preflight, "sequence": index,
                    "event_timing_available": actual_start is not None,
                    "timing_basis": "recorded_tool_start_and_duration" if actual_start else "parent_display_anchor_only",
                    "evidence_ref": _hash(str(source.relative_to(root))) if source.is_relative_to(root) else _hash(str(source))}}}
            if error:
                span["error"] = "Recorded failure; details redacted. Inspect retained local evidence."
            runs.append(span)
            references[identity] = {"file": str(source), "index": index}
            return identity

        probe_path = directory / "exec-preflight/evidence.json"
        probe = reader.read(probe_path, optional=True)
        if probe is not None:
            if not isinstance(probe, dict) or not isinstance(probe.get("billing", {}), dict):
                raise ValueError("Invalid permission-probe evidence")
            billing = probe.get("billing") or {}
            probe_id = child("permission_preflight", {"verified": probe.get("verified") is True,
                "scored": False, "tokens": _number(billing.get("tokens")), "cost": _number(billing.get("cost_usd")),
                "elapsed_sec": _number(probe.get("duration_sec")), "checks": _redact(probe.get("checks", {})),
                "inference_dispatch_attempted": probe.get("inference_dispatch_attempted") if type(probe.get("inference_dispatch_attempted")) is bool else None}, probe_path, 0,
                error=probe.get("verified") is not True)
            runs[-1]["extra"]["metadata"]["scored"] = False
            raw_path = directory / "exec-preflight/cli_records.jsonl"
            raw = reader.read(raw_path, optional=True, lines=True) or []
            for index, event in enumerate(raw):
                if event.get("type") == "item.completed":
                    item = event.get("item") or {}
                    if item.get("type") == "command_execution":
                        child("preflight_command:" + _command_name(item.get("command")), {
                            "exit_code": item.get("exit_code") if type(item.get("exit_code")) is int else None,
                            "command_sha256": _hash(item.get("command")), "status": _status(item.get("status"))},
                            raw_path, index, parent=probe_id)
                        runs[-1]["extra"]["metadata"]["scored"] = False
        for index, command in enumerate(commands):
            child("command:" + _command_name(command.get("command")), {
                "command_sha256": _hash(command.get("command")), "arguments": "redacted",
                "exit_code": command.get("exit_code") if type(command.get("exit_code")) is int else None,
                "status": _status(command.get("status")),
                "succeeded": command.get("succeeded") if type(command.get("succeeded")) is bool else None},
                directory / "execution.json", index, error=command.get("exit_code") not in (0, None))
        for index, event in enumerate(observable):
            kind = event.get("type")
            if kind not in EVENTS:
                continue
            tool = event.get("tool_name")
            child(kind, {"tool": tool if tool in TOOLS else "redacted_or_unspecified",
                "succeeded": event.get("succeeded") if type(event.get("succeeded")) is bool else None,
                "event_sha256": _hash(event)}, directory / "execution.json", index,
                error=kind in {"error", "permission_denied"} or event.get("succeeded") is False)
        tool_path = directory / "receipts/events.jsonl"
        tools = reader.read(tool_path, optional=True, lines=True)
        failures = {}
        for index, event in enumerate(tools or []):
            tool = event.get("tool")
            child("tool:" + (tool if tool in TOOLS else "redacted"), {
                "status": _status(event.get("status")), "elapsed_sec": _number(event.get("elapsed_sec")),
                "arguments_sha256": _hash(event.get("arguments")), "event_sha256": _hash(event)},
                tool_path, index, actual_start=_time(event.get("started_at")), duration=_number(event.get("elapsed_sec")),
                is_tool=True, error=event.get("status") == "failed")
            if event.get("status") == "failed":
                failures[str(tool)] = event
            elif event.get("status") == "succeeded" and str(tool) in failures:
                failed = failures.pop(str(tool))
                if failed.get("arguments") != event.get("arguments"):
                    child("tool_recovery", {"arguments_changed": True, "failed_event_sha256": _hash(failed),
                        "successful_event_sha256": _hash(event)}, tool_path, index)
        metadata["observed_broker_events"] = len(tools) if tools is not None else (0 if condition == "baseline" else None)
        metadata["harness_used_observed"] = bool(tools) if tools is not None else (False if condition == "baseline" else None)
        if details.get("errors"):
            child("coordinator_errors", {"count": len(details["errors"]), "details_sha256": _hash(details["errors"])},
                  directory / "execution.json", 0, error=True)
        if len(runs) > max_spans:
            raise ValueError("Export exceeds span budget; no payload was written")
    payload = {"record_format": FORMAT, "data_scope": data_scope, "privacy_policy": "structure_and_scalars/1",
               "manifest_sha256": manifest_hash, "runs": runs}
    if len(canonical_json_bytes(payload)) > MAX_FILE:
        raise ValueError("Trace payload exceeds byte budget")
    reader.unchanged()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    digest = _write_new(output / "payload.json", payload)
    _write_new(output / "local-evidence.json", {"payload_sha256": digest, "source_files": reader.files,
                                               "run_references": references, "uploaded": False})
    return {"payload_path": str(output / "payload.json"), "payload_sha256": digest,
            "spans": len(runs), "attempts": len(rows), "uploaded": False,
            "local_evidence_path": str(output / "local-evidence.json")}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _validate_redacted_run(run):
    """Recheck the export policy even when someone edits and rehashes a preview."""
    permitted = {"id", "parent_run_id", "name", "run_type", "start_time", "end_time", "inputs",
                 "outputs", "session_name", "tags", "extra", "error"}
    output_keys = ANSWER_KEYS | set(OUTCOMES) | {
        "redacted_text_sha256", "omitted_items", "omitted_fields", "content_sha256", "completion_status",
        "final_answer", "final_answer_availability", "final_answer_sha256", "verified", "scored", "checks",
        "inference_dispatch_attempted", "exit_code", "command_sha256", "arguments", "succeeded", "tool",
        "event_sha256", "arguments_sha256", "arguments_changed", "failed_event_sha256", "successful_event_sha256",
        "count", "details_sha256", "per_target_results"}
    texts = STATUSES | TOOLS | {"redacted", "redacted_or_unspecified", "redacted_structure", "not_loaded", "unverified_answer_hash"}
    def projected(value, depth=0):
        if depth > 16:
            return False
        if isinstance(value, str):
            return value in texts or re.fullmatch(r"[a-f0-9]{64}", value) is not None
        if isinstance(value, dict):
            return all((key in output_keys or re.fullmatch(r"field_[a-f0-9]{64}", key)) and projected(v, depth+1)
                       for key, v in value.items())
        if isinstance(value, list):
            return len(value) <= 1000 and all(projected(v, depth+1) for v in value)
        return value is None or type(value) is bool or (type(value) in (int, float) and math.isfinite(value))
    if set(run) - permitted or run.get("inputs") != {} or not projected(run.get("outputs")):
        raise ValueError("Trace violates the reviewed redaction policy")
    if run.get("error") not in (None, "Recorded failure; details redacted. Inspect retained local evidence."):
        raise ValueError("Trace error violates redaction policy")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", str(run.get("session_name"))) is None:
        raise ValueError("Trace project violates redaction policy")
    tags = run.get("tags")
    if not isinstance(tags, list) or not all(tag in {"devbench", *CONDITIONS, "classic", "biology", "public", "synthetic", "public_processed", "scored", "preflight"} for tag in tags):
        raise ValueError("Trace tags violate redaction policy")
    name = run.get("name")
    names = EVENTS | {"permission_preflight", "tool_recovery", "coordinator_errors"}
    names |= {"devbench:"+c for c in CONDITIONS} | {"tool:"+t for t in TOOLS | {"redacted"}}
    names |= {prefix+command for prefix in ("command:", "preflight_command:")
              for command in ("python", "python3", "Rscript", "bash", "sh", "zsh", "ls", "cat", "head", "tail", "wc", "rg", "grep", "git", "sed", "awk", "redacted", "unparsed")}
    if name not in names:
        raise ValueError("Trace name violates redaction policy")
    extra = run.get("extra")
    if not isinstance(extra, dict) or set(extra) != {"metadata"} or not isinstance(extra["metadata"], dict):
        raise ValueError("Trace metadata violates redaction policy")
    allowed = {"condition", "track", "data_scope", "scored", "attempt_key", "manifest_sha256", "task_identity",
        "input_sha256", "requested_model", "actual_model", "model_identity_verified", "timing_basis",
        "event_timing_available", "manifest_binding", "execution_detail_binding", "unknown_cli_event_count",
        "evidence_authority", "observed_broker_events", "harness_used_observed", "sequence", "evidence_ref"}
    literals = {*CONDITIONS, "classic", "biology", "public", "synthetic", "public_processed",
        "coordinator_dispatch_plus_adapter_duration", "artifact_mtime_display_anchor_only", "parent_display_anchor_only",
        "recorded_tool_start_and_duration", "supplied_hash_matched", "local_snapshot_only", "manifest_hash_matched",
        "unbound_local_snapshot", "local_retained_records; not_regraded_by_exporter"}
    for key, value in extra["metadata"].items():
        if key not in allowed:
            raise ValueError("Trace metadata field violates redaction policy")
        if isinstance(value, str) and not (value in literals or re.fullmatch(r"[a-f0-9]{64}", value)
                                          or (key in {"requested_model", "actual_model"} and _model(value) == value)):
            raise ValueError("Trace metadata value violates redaction policy")
        if not isinstance(value, str) and value is not None and type(value) not in (bool, int, float):
            raise ValueError("Trace metadata value violates redaction policy")


def _post_run(endpoint, api_key, workspace_id, run, timeout):
    headers = {"Content-Type": "application/json", "x-api-key": api_key}
    if workspace_id:
        headers["x-tenant-id"] = workspace_id
    request = Request(endpoint + "/runs", data=canonical_json_bytes(run), headers=headers, method="POST")
    with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=timeout) as response:
        if not 200 <= response.status < 300:
            raise OSError("LangSmith did not acknowledge the run")
        response.read(65_536)


def upload_export(payload_path, expected_sha256, output_dir, *, endpoint="https://api.smith.langchain.com",
                  timeout_sec=15, max_spans=2000, allow_upload=False):
    """Send a reviewed immutable payload once; retain ambiguous network outcomes."""
    if allow_upload is not True:
        raise PermissionError("LangSmith upload requires explicit --authorize-upload")
    if endpoint not in ENDPOINTS:
        raise ValueError("Use a supported HTTPS LangSmith endpoint; redirects are disabled")
    if type(timeout_sec) is not int or not 1 <= timeout_sec <= 60:
        raise ValueError("Upload timeout must be an integer from 1 to 60 seconds")
    if type(max_spans) is not int or not 1 <= max_spans <= MAX_SPANS:
        raise ValueError("Invalid upload span budget")
    source, output = _absolute(payload_path), _absolute(output_dir)
    reader = _Evidence(source.parent)
    payload = reader.read(source)
    if reader.digest(source) != expected_sha256:
        raise ValueError("Payload differs from the exact reviewed SHA-256")
    if (not isinstance(payload, dict) or payload.get("record_format") != FORMAT
            or payload.get("privacy_policy") != "structure_and_scalars/1"
            or payload.get("data_scope") not in {"synthetic", "public_processed"}):
        raise ValueError("Unsupported reviewed payload")
    runs = payload.get("runs")
    if not isinstance(runs, list) or not 1 <= len(runs) <= max_spans:
        raise ValueError("Upload exceeds span budget")
    ids = set()
    for run in runs:
        if not isinstance(run, dict) or run.get("run_type") not in {"chain", "tool"}:
            raise ValueError("Only observational chain/tool spans may be uploaded")
        _validate_redacted_run(run)
        identity = str(UUID(run["id"]))
        if identity in ids or (run.get("parent_run_id") is not None and run["parent_run_id"] not in ids):
            raise ValueError("Trace identifiers must be unique and parents must precede children")
        ids.add(identity)
        if _time(run.get("start_time")) is None or _time(run.get("end_time")) is None:
            raise ValueError("Trace timestamps must include their timezone")
    api_key = os.environ.get("LANGSMITH_API_KEY")
    if not api_key:
        raise RuntimeError("LANGSMITH_API_KEY is required for upload; never pass credentials as CLI arguments")
    workspace_id = os.environ.get("LANGSMITH_WORKSPACE_ID")
    if workspace_id:
        workspace_id = str(UUID(workspace_id))
    if output.exists() or source.is_relative_to(output) or output.is_relative_to(source.parent):
        raise ValueError("Upload receipt directory must be fresh and disjoint from the reviewed payload")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    receipt_path = output / "upload.json"
    receipt = {"record_format": "devbench.langsmith-upload/1", "payload_sha256": expected_sha256,
        "endpoint": endpoint, "status": "started", "planned_runs": len(runs), "confirmed_run_uploads": 0,
        "uncertain_run_uploads": 0, "runs": [], "llm_evaluations": 0, "source_evidence_modified": False}
    def save():
        descriptor, name = tempfile.mkstemp(prefix="receipt-", dir=output)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_json_bytes(receipt) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, receipt_path)
    save()
    for run in runs:
        row = {"run_id": run["id"], "status": "dispatch_pending"}
        receipt["runs"].append(row)
        receipt["uncertain_run_uploads"] = 1
        save()
        try:
            _post_run(endpoint, api_key, workspace_id, run, timeout_sec)
        except (HTTPError, URLError, OSError, ValueError, KeyboardInterrupt) as error:
            row.update(status="unconfirmed", error_kind=type(error).__name__)
            if isinstance(error, HTTPError):
                row["http_status"] = error.code
            receipt["status"] = "incomplete"
            save()
            break
        row["status"] = "acknowledged"
        receipt["confirmed_run_uploads"] += 1
        receipt["uncertain_run_uploads"] = 0
        save()
    else:
        receipt["status"] = "uploaded"
        save()
    return {**receipt, "receipt_path": str(receipt_path), "receipt_sha256": _hash(receipt_path.read_bytes()),
            "scope": "HTTP acknowledgement only; no claim of dashboard rendering or clinical qualification"}
