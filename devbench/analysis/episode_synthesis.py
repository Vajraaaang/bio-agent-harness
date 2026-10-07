"""Bounded reporting from coordinator-verified receipts, without an LLM judge.

References select observations, not numbers or scientific interpretations.
Re-rendering proves report consistency; it is not independent biological review.
"""
from collections import Counter
import hashlib
import html
import re
from pathlib import Path

from devbench.study.contracts import canonical_json_bytes
from devbench.study.evidence import _read, decode_json, verify_receipts

MAX_FINDINGS = 20
MAX_REPORT_BYTES = 2_000_000
_ID = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$"
_PRODUCERS = {"mean_contrast": "mean_condition_analysis", "cell_state_effect": "cell_type_state_analysis"}


def finding_schema():
    return {"type": "array", "maxItems": MAX_FINDINGS, "uniqueItems": True, "items": {
        "type": "object", "additionalProperties": False,
        "required": ["action", "target_id", "hypothesis_id"],
        "properties": {"action": {"type": "integer", "minimum": 1, "maximum": 100},
                       "target_id": {"type": "string", "pattern": _ID},
                       "hypothesis_id": {"type": "string", "pattern": _ID}}}}


def validate_finding_requests(value):
    if not isinstance(value, list) or len(value) > MAX_FINDINGS:
        raise ValueError("findings_must_be_a_bounded_reference_list")
    seen = set()
    for item in value:
        if (not isinstance(item, dict) or set(item) != {"action", "target_id", "hypothesis_id"}
                or type(item["action"]) is not int or not 1 <= item["action"] <= 100
                or any(not isinstance(item[key], str) or re.fullmatch(_ID, item[key]) is None
                       for key in ("target_id", "hypothesis_id"))):
            raise ValueError("finding_references_cannot_supply_values_or_unbounded_fields")
        identity = (item["action"], item["target_id"], item["hypothesis_id"])
        if identity in seen:
            raise ValueError("duplicate_finding_reference")
        seen.add(identity)
    return decode_json(canonical_json_bytes(value))


def _json(path, limit=16_000_000):
    return decode_json(_read(path, limit))


def _observation_bindings(root, node_ids):
    """Read observations named by the writer's retained immutable DAG prefix."""
    from devharness_core.provenance import canonical_json
    if (not isinstance(node_ids, list) or len(node_ids) > 2000
            or any(not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None for value in node_ids)
            or len(set(node_ids)) != len(node_ids)):
        raise ValueError("synthesis_audit_prefix_invalid")
    manifest = root / "audit/manifest.json"
    if manifest.exists() and _json(manifest)["node_ids"][:len(node_ids)] != node_ids:
        raise ValueError("synthesis_audit_prefix_not_retained")
    bindings = {}
    for index, node_id in enumerate(node_ids):
        node = _json(root / "audit/nodes" / (node_id + ".json"), 4_000_000)
        if (not isinstance(node, dict) or node.get("node_id") != node_id
                or node.get("sequence") != index + 1
                or node.get("previous") != (node_ids[index - 1] if index else None)
                or hashlib.sha256(canonical_json({k: v for k, v in node.items() if k != "node_id"})).hexdigest() != node_id):
            raise ValueError("synthesis_audit_node_changed")
        payload = node.get("payload", {})
        if node.get("kind") == "episode.observation" and "result_sha256" in payload:
            sequence = payload.get("sequence")
            if type(sequence) is not int or sequence in bindings:
                raise ValueError("synthesis_observation_sequence_invalid")
            bindings[sequence] = {**payload, "node_id": node_id}
    return bindings


def _catalog(root, config, actions, events, results, node_ids):
    """Join receipt IDs to actual checkpoint/action positions, not tool sequence."""
    from devbench.study.biology_answers import TargetAnswer
    from devbench.study.biology_broker import validate_biology_request

    event_map = {event["receipt_id"]: event for event in events}
    bindings = _observation_bindings(root, node_ids)
    positions = {}
    if len(actions) > config["limits"]["max_steps"] or len(events) > config["limits"]["max_steps"]:
        raise ValueError("synthesis_action_or_event_limit_exceeded")
    for number, action in enumerate(actions, 1):
        if action.get("kind") != "tool":
            continue
        path = root / "checkpoints" / f"action-{number:04d}" / "observation.json"
        if not path.exists():
            if number in bindings:
                raise ValueError("synthesis_recorded_observation_missing")
            continue
        content = _read(path, 16_000_000)
        observation = decode_json(content)
        if not isinstance(observation, dict):
            raise ValueError("synthesis_observation_must_be_an_object")
        receipt_id = observation.get("receipt_id")
        event = event_map.get(receipt_id)
        if (event is None or receipt_id in positions or event["tool"] != action.get("tool")
                or observation.get("status") != event["status"]
                or observation.get("input_sha256") != config["snapshot_id"]):
            raise ValueError("synthesis_checkpoint_action_receipt_mismatch")
        if event["status"] == "succeeded":
            binding = bindings.get(number, {})
            observed_hash = hashlib.sha256(content).hexdigest()
            checkpoint_bytes = _read(path.parent / "receipt.json", 1_000_000)
            checkpoint = decode_json(checkpoint_bytes)
            if (binding.get("tool") != event["tool"] or binding.get("result_sha256") != observed_hash
                    or binding.get("checkpoint_receipt", {}).get("sha256") != hashlib.sha256(checkpoint_bytes).hexdigest()
                    or not isinstance(checkpoint, dict) or checkpoint.get("stage") != f"action-{number:04d}"
                    or checkpoint.get("metadata") != {"tool": event["tool"], "receipt_id": receipt_id}
                    or checkpoint.get("artifacts", {}).get("observation.json") != observed_hash):
                raise ValueError("synthesis_observation_not_bound_to_recorded_action")
        positions[receipt_id] = number
    expected = {target["target_id"] for target in config["public_task"]["questions"]}
    latest = {}
    for event in events:
        tool, receipt_id = event["tool"], event["receipt_id"]
        if event["arguments"] != {"options": config["fixed_options"].get(tool, {})}:
            raise ValueError("synthesis_frozen_options_mismatch")
        validate_biology_request(tool, event["arguments"], config["public_task"], enforced=True)
        if event["status"] != "succeeded":
            continue
        if receipt_id not in positions:
            raise ValueError("synthesis_successful_event_has_no_action_checkpoint")
        result = results[receipt_id]
        for target in result.get("target_results", []):
            TargetAnswer.model_validate_json(canonical_json_bytes(target))
            identifier = target["target_id"]
            if identifier not in expected or _PRODUCERS.get(identifier) != tool:
                raise ValueError("synthesis_target_producer_not_supported")
            latest[identifier] = {"target": target, "event": event,
                                  "action": positions[receipt_id], "diagnostics": result.get("diagnostics", {}),
                                  "observation_node_id": bindings[positions[receipt_id]]["node_id"]}
    return latest, positions


def _resolve(requests, catalog):
    findings = []
    for request in validate_finding_requests(requests):
        entry = catalog.get(request["target_id"])
        if entry is None or entry["action"] != request["action"]:
            raise ValueError("finding_requires_the_latest_applicable_computation")
        rows = (entry["target"].get("payload") or {}).get("gene_statistics", [])
        matched = [row for row in rows if row["hypothesis_id"] == request["hypothesis_id"]]
        if len(matched) != 1:
            raise ValueError("finding_hypothesis_not_in_recorded_result")
        findings.append((request, entry, matched[0]))
    return findings


def check_finding_requests(episode, requests):
    """Preliminary reference check; terminal synthesis still verifies disk receipts."""
    if episode.broker is None:
        if validate_finding_requests(requests):
            raise ValueError("finding_has_no_tool_evidence")
        return
    catalog, _ = _catalog(episode.root, episode.config, episode.actions,
                           episode.broker.events, episode.broker.results, list(episode.audit._node_ids))
    _resolve(requests, catalog)


def _base(config, final):
    return {"format": "devbench.episode-synthesis/1", "method": "deterministic_receipt_projection",
            "question": config["question"], "assay": config["assay"], "execution_tier": config["execution_tier"],
            "snapshot_id": config["snapshot_id"], "source_sha256": config["source_sha256"],
            "outcome": final["outcome"], "reason": final["reason"],
            "termination_context": final.get("termination_context"),
            "harness_accepted": final["harness_accepted"], "scientific_correctness": None,
            "usage": final["usage"], "evidence": {"status": "not_applicable"},
            "tool_events": [], "targets": [], "findings": [], "outputs_complete": False}


def _source(entry, directory):
    event = entry["event"]
    return {"action": entry["action"], "tool": event["tool"], "receipt_id": event["receipt_id"],
            "observation_node_id": entry["observation_node_id"],
            "result_sha256": event["result_sha256"],
            "result_path": directory + "/" + event["receipt_id"] + ".json"}


def _target_summary(entry, directory):
    from devbench.study.biology_answers import ComputedConditionContrast
    target = entry["target"]
    payload = target.get("payload") or {}
    rows = payload.get("gene_statistics", [])
    contrast = payload.get("computed_contrast")
    if target["estimate_status"] == "estimated":
        if contrast is None:
            raise ValueError("synthesis_computed_contrast_unavailable")
        ComputedConditionContrast.model_validate_json(canonical_json_bytes(contrast))
    counts = dict(Counter(row["estimate_status"] for row in rows))
    # These two receipt-bound producers both use _bh over all attempted rows.
    # The public multiplicity_scope contains only rows with actual tests.
    return {"target_id": target["target_id"], "source": _source(entry, directory),
            "requested": {key: target[key] for key in (
                "estimand", "contrast", "units", "reference_population", "prediction_reference")},
            "claim_type": target["claim_type"], "estimate_status": target["estimate_status"],
            "evidence_scope": target["evidence_scope"], "computed_contrast": contrast,
            "biological_unit": payload.get("biological_unit"), "analysis_layer": payload.get("analysis_layer"),
            "selection_conditional_inference": payload.get("selection_conditional_inference"),
            "aggregation_definition": payload.get("aggregation_definition"),
            "prediction_structure": payload.get("prediction_structure", "coordinate_specific"),
            "limitation_codes": target.get("limitation_codes", []), "support_checks": target.get("support_checks", []),
            "hypothesis_family": {"attempted_count": len(rows),
                "tested_count": len(payload.get("multiplicity_scope", [])), "status_counts": counts,
                "adjustment": "Benjamini_Hochberg_over_all_attempted_hypotheses" if rows else None,
                "unavailable_policy": "internal_p_one_positions_public_p_q_remain_missing" if rows else None}}


def _replication(entry, component):
    diagnostic = entry["diagnostics"]
    if component is None:
        units = diagnostic.get("biological_units", {})
        return {key: units[key] for key in ("analysis_unit_count", "library_count", "population_unit") if key in units}
    design = diagnostic.get("cell_state_effect", {}).get("fits_by_type", {}).get(component, {}).get("design", {})
    return {key: design[key] for key in ("independent_donors_per_condition", "paired_donor_blocking") if key in design}


def _event_summary(event, result, position, directory):
    diagnostics = (result or {}).get("diagnostics", {})
    design = diagnostics.get("design", {})
    return {"action": position, "tool": event["tool"], "receipt_id": event["receipt_id"],
            "status": event["status"], "elapsed_sec": event.get("elapsed_sec"), "error": event.get("error"),
            "diagnostic_status": diagnostics.get("status"),
            "design_check": {key: design[key] for key in (
                "rank", "column_count", "condition_number_validated", "condition_number", "residual_df") if key in design},
            "result_path": directory + "/" + event["receipt_id"] + ".json" if event["status"] == "succeeded" else None}


def build_synthesis(root, config, final, actions, *, allow_evidence_read=False):
    if allow_evidence_read is not True:
        raise PermissionError("Synthesis requires coordinator evidence-reading authorization")
    root = Path(root)
    report = _base(config, final)
    requests = validate_finding_requests(final.get("finding_requests", []))
    if requests and (not actions or actions[-1].get("kind") != "finish" or actions[-1].get("findings") != requests):
        raise ValueError("synthesis_requests_not_bound_to_terminal_action")
    receipt, directory = final.get("broker_receipt"), final.get("broker_directory")
    if receipt is None and directory is None:
        if requests or final["outcome"] == "completed":
            raise ValueError("synthesis_requires_tool_receipts")
        return report
    if not isinstance(directory, str) or re.fullmatch(r"tool-evidence(?:-resume-[0-9]{4})?", directory) is None:
        raise ValueError("synthesis_broker_directory_unavailable_or_invalid")
    from devbench.study.broker import BrokerPolicy
    byte_limit = BrokerPolicy.__dataclass_fields__["max_evidence_bytes"].default
    folder = root / directory
    total = 0
    entries = list(folder.iterdir())
    if len(entries) > 2 * config["limits"]["max_steps"] + 4:
        raise ValueError("synthesis_evidence_exceeds_episode_policy")
    for path in entries:
        # The broker keeps disposable numerical scratch alongside receipts.
        # Its contents are not receipt evidence and are never read here.
        if path.name == "scratch" and path.is_dir() and not path.is_symlink():
            continue
        if path.is_symlink() or not path.is_file():
            raise ValueError("synthesis_evidence_must_be_regular_files")
        total += path.stat().st_size
        if total > byte_limit:
            raise ValueError("synthesis_evidence_exceeds_episode_policy")
    evidence = verify_receipts(folder, expected_receipt=receipt, expected_input_sha256=config["snapshot_id"],
        expected_implementation_sha256=config["source_sha256"], allow_evidence_read=True)
    if evidence.execution_scoping or evidence.condition != "enforced":
        raise ValueError("synthesis_broker_scope_not_supported")
    catalog, positions = _catalog(root, config, actions, evidence.events, evidence.results, final.get("audit_prefix_node_ids"))
    report["evidence"] = {"status": "verified", "root_receipt": evidence.root_receipt,
                          "scope": "recorded_execution_consistency_not_biological_correctness"}
    report["tool_events"] = [_event_summary(event, evidence.results.get(event["receipt_id"]),
        positions.get(event["receipt_id"]), directory) for event in evidence.events]
    report["targets"] = [_target_summary(entry, directory) for entry in catalog.values()]
    for request, entry, row in _resolve(requests, catalog):
        report["findings"].append({"target_id": request["target_id"], "source": _source(entry, directory),
                                  "estimate": row, "replication": _replication(entry, row.get("component_id"))})
    expected = {target["target_id"] for target in config["public_task"]["questions"]}
    report["outputs_complete"] = set(catalog) == expected and all(
        item["estimate_status"] == "estimated" and item["hypothesis_family"]["attempted_count"] > 0
        and set(item["hypothesis_family"]["status_counts"]) == {"estimated"} for item in report["targets"])
    if len(canonical_json_bytes(report)) > MAX_REPORT_BYTES:
        raise ValueError("synthesis_exceeds_report_byte_limit")
    return report


def safe_synthesis(root, config, final, actions):
    """Evidence failure withholds every numerical claim; callers deny acceptance."""
    try:
        return build_synthesis(root, config, final, actions, allow_evidence_read=True)
    except (OSError, ValueError, TypeError, KeyError) as error:
        report = _base(config, final)
        reason = str(error).replace(str(root), "<episode>")[:2000]
        report["evidence"] = {"status": "withheld", "error_type": type(error).__name__, "reason": reason}
        return report


def _text(value):
    escaped = html.escape(str(value), quote=False).replace("\n", " ").replace("\r", " ")
    return re.sub(r"([\\`*_{}\[\]()#+|>~])", r"\\\1", escaped)


def render_synthesis(report):
    lines = ["# RNA-seq analysis episode", "", "Question supplied: " + _text(report["question"]), "",
             "Outcome: " + _text(report["outcome"]) + ". Reason: " + _text(report["reason"]), "",
             "Execution tier: " + _text(report["execution_tier"]), "",
             "Receipt evidence: " + _text(report["evidence"]["status"]), "",
             "Scientific correctness has not been scored against independent references. "
             "This report does not establish clinical validation, direct causality or harness benefit.", ""]
    if report["evidence"]["status"] == "withheld":
        lines += ["Numerical statements withheld: " + _text(report["evidence"]["reason"]), ""]
    for target in report["targets"]:
        lines += ["## " + _text(target["target_id"]), "", "Requested wording (not a computed quantity): "
                  + _text(target["requested"]["contrast"]) + "; requested units: " + _text(target["requested"]["units"]), ""]
        contrast = target["computed_contrast"]
        if contrast:
            from devbench.study.biology_answers import ComputedConditionContrast
            computed = ComputedConditionContrast.model_validate_json(canonical_json_bytes(contrast))
            lines += ["Computed contrast: " + _text(computed.describe()), "",
                      "Biological unit: " + _text(target["biological_unit"]) + "; evidence scope: " + _text(target["evidence_scope"]), ""]
        family = target["hypothesis_family"]
        lines += [f"Attempted hypotheses: {family['attempted_count']}; reported tests: {family['tested_count']}. "
                  + "Status counts: " + ", ".join(_text(status) + ": " + str(count)
                      for status, count in family["status_counts"].items()), ""]
        if family["adjustment"]:
            lines += ["BH adjustment includes all attempted hypotheses; unavailable positions retain internal p=1, "
                      "with no fabricated public p/q values. Highlight selection does not redefine this family.", ""]
        lines += ["Conditional inference flag: " + _text(target["selection_conditional_inference"]) + ". "
                  "Intervals and tests retain the fitted model's assumptions; empirical calibration is not established here.", "",
                  "Limitations supplied by the tool: " + "; ".join(_text(code.replace("_", " ")) for code in target["limitation_codes"]), "",
                  "[Full recorded numerical result](" + target["source"]["result_path"] + ")", ""]
        if target["aggregation_definition"]:
            lines += ["Aggregation: " + _text(target["aggregation_definition"]), ""]
        for check in target["support_checks"]:
            lines += ["Support check: " + _text(check["code"]) + " — " + _text(check["status"])
                      + ". " + _text(check["explanation"]), ""]
    lines += ["## Selected observations", ""]
    if not report["findings"]:
        lines += ["No individual hypotheses were selected for highlighting.", ""]
    for finding in report["findings"]:
        row = finding["estimate"]
        lines += ["- " + _text(row["hypothesis_id"]) + " (" + _text(row.get("gene_id")) + ", "
                  + _text(row.get("component_id")) + "): " + _text(row["estimate_status"])]
        if row["estimate_status"] == "estimated":
            for index, value in enumerate(row.get("values", [])):
                interval = row.get("intervals", [])[index] if index < len(row.get("intervals", [])) else None
                text = "  " + _text(row["coordinate_ids"][index]) + f": estimate={value:.8g}"
                if interval:
                    text += f"; {interval['confidence_level'] * 100:g}% interval [{interval['lower']:.8g}, {interval['upper']:.8g}]"
                lines.append(text + ".")
            lines.append("  p=" + (str(row["p_value"]) if row.get("p_value") is not None else "unavailable")
                         + "; q=" + (str(row["q_value"]) if row.get("q_value") is not None else "unavailable") + ".")
        else:
            lines.append("  No estimate or test reported: " + _text(row.get("missing_reason")))
        if finding["replication"]:
            lines.append("  Recorded replication: " + _text(finding["replication"]) + ".")
        lines += ["  Action " + str(finding["source"]["action"]) + "; receipt `" + finding["source"]["receipt_id"] + "`.", ""]
    lines += ["## Execution and limitations", "", "Tool events: " + str(len(report["tool_events"])) + ". "
              "Failed tool events: " + str(sum(x["status"] != "succeeded" for x in report["tool_events"])) + ".", ""]
    for event in report["tool_events"]:
        lines += ["- Action " + _text(event["action"]) + ": " + _text(event["tool"]) + "; execution " + _text(event["status"])]
        if event["diagnostic_status"] is not None or event["design_check"]:
            lines.append("  Recorded diagnostics: " + _text(event["diagnostic_status"])
                         + "; design: " + _text(event["design_check"]))
        if event["error"]:
            lines.append("  Recorded tool error: " + _text(event["error"]))
        if event["result_path"]:
            lines.append("  [Result and diagnostics](" + event["result_path"] + ")")
        lines.append("")
    usage = report["usage"]
    for key, label in (("elapsed_sec", "Recorded elapsed seconds"), ("RSS_MB", "Sampled process-tree RSS in MiB"),
                       ("memory_coverage", "Memory observation coverage"), ("model", "Observed model"),
                       ("tokens", "Observable model tokens"), ("cost", "Model cost")):
        lines += [label + ": " + (_text(usage[key]) if usage.get(key) is not None else "unknown") + ".", ""]
    if report["termination_context"]:
        lines += ["Recorded termination request before receipt verification: " + _text(report["termination_context"]), ""]
    lines += ["Input snapshot: `" + report["snapshot_id"] + "`. Implementation: `" + report["source_sha256"] + "`.", "",
              "This is a deterministic projection of recorded outputs. It does not validate free-form interpretation "
              "or supply an independent scientific review. Nonsignificance does not establish equivalence or absence.", ""]
    result = "\n".join(lines)
    if len(result.encode()) > MAX_REPORT_BYTES:
        raise ValueError("synthesis_markdown_exceeds_report_byte_limit")
    return result


def verify_synthesis(root, expected_root, *, allow_evidence_read=False):
    if allow_evidence_read is not True:
        raise PermissionError("Report verification requires evidence-reading authorization")
    from .episodes import verify_episode, _sources
    from .checkpoints import digest
    root = Path(root)
    verify_episode(root, expected_root)
    config, final, actions = (_json(root / name) for name in ("parameters.json", "episode.json", "actions.json"))
    if digest(_sources()) != config["source_sha256"]:
        raise ValueError("Report verification requires its recorded source version")
    recorded = _json(root / "synthesis.json", MAX_REPORT_BYTES)
    rebuilt = safe_synthesis(root, config, final, actions)
    if recorded != rebuilt or _read(root / "summary.md", MAX_REPORT_BYTES).decode() != render_synthesis(rebuilt):
        raise ValueError("synthesis_does_not_match_verified_result_projection")
    return {"valid": True, "evidence_verified": rebuilt["evidence"]["status"] == "verified",
            "finding_count": len(rebuilt["findings"]),
            "scope": "report_consistency_not_independent_scientific_validation"}
