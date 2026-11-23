"""Bounded reporting from coordinator-verified receipts, without an LLM judge.

References select observations, not numbers or scientific interpretations.
Re-rendering proves report consistency; it is not independent biological review.
"""

import csv
import hashlib
import html
import io
import math
import re
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree

from devbench.core._files import read_bounded_json
from devbench.study.contracts import canonical_json_bytes
from devbench.study.evidence import _read, decode_json, verify_receipts

MAX_FINDINGS = 20
MAX_REPORT_BYTES = 2_000_000
SYNTHESIS_V1 = "devbench.episode-synthesis/1"
SYNTHESIS_V2 = "devbench.episode-synthesis/2"
PRIMARY_BRANCH_ID = "primary"
_ID = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$"
_PRODUCERS = {
    "mean_contrast": "mean_condition_analysis",
    "cell_state_effect": "cell_type_state_analysis",
}
_PLOT_SIGNATURES = {".png": b"\x89PNG\r\n\x1a\n", ".pdf": b"%PDF-"}


def target_family_complete(target, gene_ids):
    """Require a complete reported family, not an estimate for every gene.

    Typed result/evidence validation still owns estimate validity. This gate
    permits honest non-estimates but never counts them as reported tests.
    Component-specific hypotheses may share a gene, so family coverage uses
    gene identities while hypothesis identities must remain distinct.
    """
    if (
        not isinstance(target, dict)
        or target.get("estimate_status") != "estimated"
        or target.get("claim_type") not in {"supported_estimate", "restricted_estimate"}
    ):
        return False
    payload = target.get("payload")
    rows = payload.get("gene_statistics") if isinstance(payload, dict) else None
    if not isinstance(rows, (list, tuple)) or not rows:
        return False
    if (
        not isinstance(gene_ids, (list, tuple))
        or not gene_ids
        or any(not isinstance(gene, str) or not gene for gene in gene_ids)
        or len(set(gene_ids)) != len(gene_ids)
    ):
        return False
    genes, hypotheses, estimated = set(), set(), 0
    for row in rows:
        if not isinstance(row, dict):
            return False
        hypothesis = row.get("hypothesis_id")
        gene = row.get("gene_id") or hypothesis
        if (
            not isinstance(hypothesis, str)
            or not hypothesis
            or hypothesis in hypotheses
            or not isinstance(gene, str)
            or not gene
            or "p_value" not in row
            or "q_value" not in row
        ):
            return False
        hypotheses.add(hypothesis)
        genes.add(gene)
        if not _valid_family_test(row):
            return False
        estimated += row.get("estimate_status") == "estimated"
    return estimated > 0 and genes == set(gene_ids)


def _valid_family_test(row):
    p_value, q_value = row["p_value"], row["q_value"]
    if row.get("estimate_status") == "estimated":
        return (
            all(
                not isinstance(value, bool)
                and isinstance(value, (int, float))
                and math.isfinite(value)
                for value in (p_value, q_value)
            )
            and 0 <= p_value <= q_value <= 1
        )
    if row.get("estimate_status") in {"failed", "unavailable"}:
        reason = row.get("missing_reason")
        return (
            p_value is None
            and q_value is None
            and isinstance(reason, str)
            and bool(reason.strip())
        )
    return False


ANALYST_DISPOSITIONS = (
    "candidate",
    "limited_count",
    "uncertain_effect",
    "not_assessable",
    "condition_restricted_candidate",
    "single_unit_dominance_candidate",
)


def finding_schema(*, analyst=False, branch_aware=False):
    legacy = {
        "type": "object",
        "additionalProperties": False,
        "required": ["action", "target_id", "hypothesis_id"],
        "properties": {
            "action": {"type": "integer", "minimum": 1, "maximum": 100},
            "target_id": {"type": "string", "pattern": _ID},
            "hypothesis_id": {"type": "string", "pattern": _ID},
        },
    }
    if branch_aware:
        legacy["properties"]["branch_id"] = {"type": "string", "pattern": _ID}
    typed = {
        **legacy,
        "required": [
            *legacy["required"],
            "result_sha256",
            "disposition",
            "evidence_refs",
            "field_refs",
            "qualification",
        ],
        "properties": {
            **legacy["properties"],
            "result_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
            "disposition": {"enum": list(ANALYST_DISPOSITIONS)},
            "evidence_refs": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "uniqueItems": True,
                "items": {"type": "string", "pattern": r"^query:[1-9][0-9]{0,2}$"},
            },
            "field_refs": {
                "type": "array",
                "minItems": 1,
                "maxItems": 12,
                "uniqueItems": True,
                "items": {
                    "type": "string",
                    "maxLength": 500,
                    "pattern": r"^query:[1-9][0-9]{0,2}/query_result/.+$",
                },
            },
            "qualification": {"type": "string", "minLength": 1, "maxLength": 1500},
        },
    }
    return {
        "type": "array",
        "maxItems": MAX_FINDINGS,
        "uniqueItems": True,
        "items": {"oneOf": [legacy, typed]} if analyst else legacy,
    }


def validate_finding_requests(value, *, analyst=False, branch_aware=False):
    if not isinstance(value, list) or len(value) > MAX_FINDINGS:
        raise ValueError("findings_must_be_a_bounded_reference_list")
    from jsonschema import Draft202012Validator

    schema = finding_schema(analyst=analyst, branch_aware=branch_aware)
    if not Draft202012Validator(schema).is_valid(value):
        raise ValueError("finding_references_cannot_supply_values_or_unbounded_fields")
    seen = set()
    for item in value:
        if type(item["action"]) is not int:
            raise ValueError("finding_action_must_be_integer")
        if any(
            re.fullmatch(_ID, item[key]) is None
            for key in ("target_id", "hypothesis_id", "branch_id")
            if key in item
        ):
            raise ValueError("finding_references_require_exact_bounded_identifiers")
        identity = (item["action"], item["target_id"], item["hypothesis_id"])
        if identity in seen:
            raise ValueError("duplicate_finding_reference")
        seen.add(identity)
    return decode_json(canonical_json_bytes(value))


def action_branch_id(action):
    identifier = action.get("branch_id", PRIMARY_BRANCH_ID)
    if not isinstance(identifier, str) or re.fullmatch(_ID, identifier) is None:
        raise ValueError("invalid_branch_id")
    return identifier


def _json(path, limit=16_000_000):
    return read_bounded_json(path, limit)


def _observation_bindings(root, node_ids, *, recorded_actions=None):
    """Read observations named by the writer's retained immutable DAG prefix."""
    from devharness_core.provenance import canonical_json

    if (
        not isinstance(node_ids, list)
        or len(node_ids) > 2000
        or any(
            not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None
            for value in node_ids
        )
        or len(set(node_ids)) != len(node_ids)
    ):
        raise ValueError("synthesis_audit_prefix_invalid")
    manifest = root / "audit/manifest.json"
    if manifest.exists() and _json(manifest)["node_ids"][: len(node_ids)] != node_ids:
        raise ValueError("synthesis_audit_prefix_not_retained")
    bindings = {}
    for index, node_id in enumerate(node_ids):
        node = _json(root / "audit/nodes" / (node_id + ".json"), 4_000_000)
        if (
            not isinstance(node, dict)
            or node.get("node_id") != node_id
            or node.get("sequence") != index + 1
            or node.get("previous") != (node_ids[index - 1] if index else None)
            or hashlib.sha256(
                canonical_json({k: v for k, v in node.items() if k != "node_id"})
            ).hexdigest()
            != node_id
        ):
            raise ValueError("synthesis_audit_node_changed")
        payload = node.get("payload", {})
        if recorded_actions is not None and node.get("kind") == "episode.action":
            sequence = payload.get("sequence")
            if type(sequence) is not int or sequence in recorded_actions:
                raise ValueError("synthesis_action_sequence_invalid")
            recorded_actions[sequence] = payload["action"]
        if node.get("kind") == "episode.observation" and "result_sha256" in payload:
            sequence = payload.get("sequence")
            if type(sequence) is not int or sequence in bindings:
                raise ValueError("synthesis_observation_sequence_invalid")
            bindings[sequence] = {**payload, "node_id": node_id}
    return bindings


def _succeeded_checkpoint(
    root,
    config,
    action,
    number,
    binding,
    event,
    observation,
    observed_hash,
):
    path = root / "checkpoints" / f"action-{number:04d}" / "observation.json"
    checkpoint_bytes = _read(path.parent / "receipt.json", 1_000_000)
    checkpoint = decode_json(checkpoint_bytes)
    expected_metadata = {"tool": event["tool"], "receipt_id": event["receipt_id"]}
    specification = None
    if binding.get("specification_id") is not None:
        from .scientific_analysis_spec import verify_retained_specification

        specification = verify_retained_specification(
            root,
            config,
            action,
            number,
            binding,
            event,
            observation,
            checkpoint,
        )
        expected_metadata["specification_id"] = binding["specification_id"]
    if (
        binding.get("tool") != event["tool"]
        or binding.get("result_sha256") != observed_hash
        or binding.get("checkpoint_receipt", {}).get("sha256")
        != hashlib.sha256(checkpoint_bytes).hexdigest()
        or not isinstance(checkpoint, dict)
        or checkpoint.get("stage") != f"action-{number:04d}"
        or checkpoint.get("metadata") != expected_metadata
        or not isinstance(checkpoint.get("artifacts"), dict)
        or checkpoint["artifacts"].get("observation.json") != observed_hash
    ):
        raise ValueError("synthesis_observation_not_bound_to_recorded_action")
    return specification, checkpoint["artifacts"]


def _result_checkpoints(root, config, actions, events, node_ids, *, branch_aware):
    from .episodes import episode_evidence_limits

    if (
        len(actions) > config["limits"]["max_steps"]
        or len(events) > config["limits"]["max_steps"]
    ):
        raise ValueError("synthesis_action_or_event_limit_exceeded")
    event_map = {event["receipt_id"]: event for event in events}
    recorded_actions = {} if branch_aware else None
    bindings = _observation_bindings(root, node_ids, recorded_actions=recorded_actions)
    positions, scientific_specs, artifacts = {}, {}, {}
    for number, action in enumerate(actions, 1):
        if action.get("kind") != "tool":
            continue
        path = root / "checkpoints" / f"action-{number:04d}" / "observation.json"
        if not path.exists():
            if number in bindings:
                raise ValueError("synthesis_recorded_observation_missing")
            continue
        if recorded_actions is not None and recorded_actions.get(number) != action:
            raise ValueError("synthesis_action_not_bound_to_audit")
        content = _read(path, episode_evidence_limits(config)["max_checkpoint_bytes"])
        observation = decode_json(content)
        if not isinstance(observation, dict):
            raise ValueError("synthesis_observation_must_be_an_object")
        receipt_id = observation.get("receipt_id")
        event = event_map.get(receipt_id)
        if (
            event is None
            or receipt_id in positions
            or event["tool"] != action.get("tool")
            or observation.get("status") != event["status"]
            or observation.get("input_sha256") != config["snapshot_id"]
        ):
            raise ValueError("synthesis_checkpoint_action_receipt_mismatch")
        if event["status"] == "succeeded":
            scientific_specs[receipt_id], artifacts[receipt_id] = _succeeded_checkpoint(
                root,
                config,
                action,
                number,
                bindings.get(number, {}),
                event,
                observation,
                hashlib.sha256(content).hexdigest(),
            )
        positions[receipt_id] = number
    return positions, scientific_specs, bindings, artifacts


def _catalog(root, config, actions, events, results, node_ids, *, branch_aware=False):
    """Join receipt IDs to actual checkpoint and action positions."""
    from devbench.study.biology_answers import TargetAnswer
    from devbench.study.biology_broker import (
        biology_tool_menu,
        validate_biology_request,
    )

    from .episodes import _proposed_parameters, resolve_scientific_options

    positions, scientific_specs, bindings, artifacts = _result_checkpoints(
        root,
        config,
        actions,
        events,
        node_ids,
        branch_aware=branch_aware,
    )
    task = config["public_task"]
    menu = biology_tool_menu(task) if branch_aware else {}
    expected = {target["target_id"] for target in task["questions"]}
    latest = {}
    for event in events:
        tool, receipt_id = event["tool"], event["receipt_id"]
        action_number = positions.get(receipt_id)
        # An abort can retain a broker failure before its observation is written.
        # V2 keeps that failure without assigning it to an action or branch.
        unbound_v2 = branch_aware and action_number is None
        if scientific_specs.get(receipt_id) is None and not unbound_v2:
            options = config["fixed_options"].get(tool, {})
            if branch_aware:
                action = actions[action_number - 1]
                options = resolve_scientific_options(
                    tool,
                    _proposed_parameters(action),
                    options,
                    task,
                    menu[tool]["driver"],
                )
            if event["arguments"] != {"options": options}:
                raise ValueError("synthesis_frozen_options_mismatch")
        validate_biology_request(tool, event["arguments"], task, enforced=True)
        if event["status"] != "succeeded":
            continue
        if action_number is None:
            raise ValueError("synthesis_successful_event_has_no_action_checkpoint")
        result = results[receipt_id]
        branch = (
            action_branch_id(actions[action_number - 1])
            if branch_aware
            else PRIMARY_BRANCH_ID
        )
        for target in result.get("target_results", []):
            TargetAnswer.model_validate_json(canonical_json_bytes(target))
            identifier = target["target_id"]
            if identifier not in expected or _PRODUCERS.get(identifier) != tool:
                raise ValueError("synthesis_target_producer_not_supported")
            latest[branch, identifier] = {
                "target": target,
                "event": event,
                "action": action_number,
                "diagnostics": _receipt_diagnostics(result),
                "observation_node_id": bindings[action_number]["node_id"],
                **({"branch_id": branch} if branch_aware else {}),
            }
    return latest, positions, scientific_specs, artifacts


def _resolve(requests, catalog, *, analyst=False, branch_aware=False):
    by_action = {
        (entry["action"], target_id): entry for (_, target_id), entry in catalog.items()
    }
    findings = []
    validated = validate_finding_requests(
        requests,
        analyst=analyst,
        branch_aware=branch_aware,
    )
    for request in validated:
        entry = by_action.get((request["action"], request["target_id"]))
        if entry is None:
            raise ValueError("finding_requires_the_latest_applicable_computation")
        if "branch_id" in request and request["branch_id"] != entry["branch_id"]:
            raise ValueError("finding_branch_action_mismatch")
        rows = (entry["target"].get("payload") or {}).get("gene_statistics", [])
        matched = [
            row for row in rows if row["hypothesis_id"] == request["hypothesis_id"]
        ]
        if len(matched) != 1:
            raise ValueError("finding_hypothesis_not_in_recorded_result")
        findings.append((request, entry, matched[0]))
    return findings


def check_finding_requests(episode, requests):
    """Check live references and return the branch catalog for completion checks."""
    from .episode_agent import analyst_policy

    analyst = analyst_policy(episode)
    if episode.broker is None:
        if validate_finding_requests(requests, analyst=analyst, branch_aware=True):
            raise ValueError("finding_has_no_tool_evidence")
        return {}
    catalog, _, _, _ = _catalog(
        episode.root,
        episode.config,
        episode.actions,
        episode.broker.events,
        episode.broker.results,
        list(episode.audit._node_ids),
        branch_aware=True,
    )
    resolved = _resolve(requests, catalog, analyst=analyst, branch_aware=True)
    if analyst:
        _analyst_findings(
            episode.root,
            episode.config,
            episode.actions,
            resolved,
            getattr(episode, "query_receipts", []),
        )
    return catalog


def branch_outputs_complete(config, catalog, branch_id=PRIMARY_BRANCH_ID):
    targets = {
        target_id: entry["target"]
        for (branch, target_id), entry in catalog.items()
        if branch == branch_id
    }
    task = config["public_task"]
    expected = {target["target_id"] for target in task["questions"]}
    return set(targets) == expected and all(
        target_family_complete(target, task["gene_ids"]) for target in targets.values()
    )


def _query_field(payload, pointer, gene, hypothesis):
    """Resolve an exact JSON pointer while prohibiting another gene's evidence."""
    parts = pointer.split("/")
    if parts[0] != "query_result" or len(parts) < 2:
        raise ValueError("analyst_finding_field_reference_invalid")
    parts = [part.replace("~1", "/").replace("~0", "~") for part in parts[1:]]
    if parts[0] == "rows":
        if len(parts) < 3 or not re.fullmatch(r"0|[1-9][0-9]*", parts[1]):
            raise ValueError("analyst_finding_requires_concrete_gene_row")
        try:
            row = payload["rows"][int(parts[1])]
        except (IndexError, KeyError):
            raise ValueError("analyst_finding_gene_row_absent") from None
        if row.get("gene_id") != gene or (
            "hypothesis_id" in row and row["hypothesis_id"] != hypothesis
        ):
            raise ValueError("analyst_finding_cross_gene_reference")
    elif parts[0] not in {"status", "missing_reason", "unavailable_fields"}:
        raise ValueError("analyst_finding_field_not_scientific_evidence")
    value = payload
    try:
        for part in parts:
            value = (
                value[int(part)]
                if isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", part)
                else value[part]
            )
    except (IndexError, KeyError, TypeError):
        raise ValueError("analyst_finding_field_unavailable") from None
    return value


def _declared_pairing(root, config, pairs):
    """Check explicit sample pairs against declared metadata, never infer identity."""
    import csv
    import io

    from .checkpoints import sha256

    snapshot = _json(root / "snapshot.json")
    path = root / snapshot["metadata"]["path"]
    if sha256(path) != snapshot["metadata"]["sha256"]:
        raise ValueError("analyst_pairing_metadata_changed")
    task = config["public_task"]
    identity = task.get("observation_id_label", "sample_id")
    unit = task.get("population_unit_label") or task.get("unit_hierarchy", {}).get(
        "population_unit_label"
    )
    condition = task.get("analysis_columns", {}).get("condition", "condition")
    reader = csv.DictReader(io.StringIO(_read(path, 8_000_000).decode()))
    if any(key not in (reader.fieldnames or []) for key in (identity, unit, condition)):
        raise ValueError("analyst_declared_pairing_columns_unavailable")
    metadata = {}
    for row in reader:
        if row[identity] in metadata:
            raise ValueError("analyst_pairing_sample_ambiguous")
        metadata[row[identity]] = row
    seen_samples, units, reference_groups, comparison_groups = (
        set(),
        set(),
        set(),
        set(),
    )
    for pair in pairs:
        reference, comparison = pair["reference"], pair["comparison"]
        if reference == comparison or {reference, comparison} & seen_samples:
            raise ValueError("analyst_pairing_requires_disjoint_samples")
        a, b = metadata.get(reference), metadata.get(comparison)
        if (
            a is None
            or b is None
            or not a[unit]
            or a[unit] != b[unit]
            or a[unit] in units
        ):
            raise ValueError("analyst_pairing_requires_distinct_declared_units")
        units.add(a[unit])
        seen_samples.update((reference, comparison))
        reference_groups.add(a[condition])
        comparison_groups.add(b[condition])
    if (
        len(units) < 2
        or len(reference_groups) != 1
        or len(comparison_groups) != 1
        or not next(iter(reference_groups), "").strip()
        or not next(iter(comparison_groups), "").strip()
        or reference_groups == comparison_groups
    ):
        raise ValueError(
            "analyst_pairing_requires_two_declared_conditions_and_multiple_units"
        )
    return {
        "status": "metadata_declarations_consistent",
        "population_unit_label": unit,
        "distinct_declared_units": len(units),
        "scope": "supplied_identity_declarations_not_independent_identity_validation",
    }


def _finding_queries(root, actions, request, receipts, gene):
    from .checkpoints import digest

    queries = {}
    for reference in request["evidence_refs"]:
        number = int(reference.split(":")[1])
        if (
            not 1 <= number <= len(actions)
            or actions[number - 1].get("kind") != "query_evidence"
        ):
            raise ValueError("analyst_finding_query_reference_invalid")
        action = actions[number - 1]
        matched = [receipt for receipt in receipts if receipt["action"] == number]
        if (
            action.get("source_action") != request["action"]
            or len(matched) != 1
            or matched[0].get("source", {}).get("result_sha256")
            != request["result_sha256"]
            or action["query"].get("target_id", request["target_id"])
            != request["target_id"]
            or (
                "gene_ids" in action["query"]
                and gene not in action["query"]["gene_ids"]
            )
        ):
            raise ValueError("analyst_finding_query_source_mismatch")
        path = root / "checkpoints" / f"query-{number:04d}" / "query-observation.json"
        retained = _json(path, 12 * 1024)
        if (
            retained.get("query_receipt") != matched[0]
            or digest(retained["query_result"]) != matched[0]["response_sha256"]
        ):
            raise ValueError("analyst_finding_query_content_changed")
        queries[reference] = (action, retained["query_result"])
    return queries


def _finding_observations(request, queries, gene):
    observations, used, support = [], set(), []
    for field in request["field_refs"]:
        reference, pointer = field.split("/", 1)
        if reference not in queries:
            raise ValueError("analyst_finding_field_requires_cited_query")
        action, payload = queries[reference]
        value = _query_field(payload, pointer, gene, request["hypothesis_id"])
        observations.append({"field_ref": field, "value": value})
        support.append((action, payload, pointer, value))
        used.add(reference)
    if used != set(queries):
        raise ValueError("analyst_finding_uncited_query_fields")
    return observations, support


def _check_finding_disposition(disposition, fitted_row, support):
    unavailable = any(
        payload.get("status") == "unavailable"
        or (
            pointer.endswith(("estimate_status", "annotation_status", "status"))
            and value in ("failed", "unavailable", "ambiguous")
        )
        for _, payload, pointer, value in support
        if isinstance(value, (str, int, float, bool, type(None)))
    )
    statistics = any(
        action["query"].get("mode", "gene_statistics") == "gene_statistics"
        and "/rows/" in "/" + pointer
        and any(
            "/" + key in pointer
            for key in ("values", "p_value", "q_value", "intervals")
        )
        for action, _, pointer, _ in support
    )
    zero_counts = any(
        action["query"].get("mode") == "counts"
        and "/counts" in pointer
        and (
            any(number == 0 for number in value.values())
            if isinstance(value, dict)
            else value == 0
        )
        for action, _, pointer, value in support
    )
    if disposition == "not_assessable" and not (
        unavailable or fitted_row.get("estimate_status") != "estimated"
    ):
        raise ValueError("analyst_not_assessable_requires_unavailable_evidence")
    if disposition == "limited_count" and not zero_counts:
        raise ValueError("analyst_limited_count_requires_observed_zero_counts")
    if disposition in {"candidate", "uncertain_effect"} and not (
        statistics and fitted_row.get("estimate_status") == "estimated"
    ):
        raise ValueError("analyst_candidate_requires_retained_gene_statistics")


def _finding_unit_pairing(root, config, request, queries, gene):
    candidates = []
    for reference, (action, payload) in queries.items():
        if (
            action["query"].get("mode") != "paired"
            or payload.get("layer") != "normalized"
        ):
            continue
        if not any(
            field.startswith(reference + "/query_result/rows/")
            and any("/" + key in field for key in ("pairs", "descriptive_dominance"))
            for field in request["field_refs"]
        ):
            continue
        matching = [
            row for row in payload.get("rows", []) if row.get("gene_id") == gene
        ]
        if len(matching) == 1 and "pairs" in matching[0]:
            candidates.append((action, matching[0]))
    if not candidates:
        raise ValueError("analyst_unit_candidate_requires_normalized_pair_evidence")
    action, pair_row = candidates[0]
    pairing = _declared_pairing(
        root, config, action["query"].get("pairs") or pair_row["pairs"]
    )
    if request["disposition"] == "condition_restricted_candidate":
        reference = [pair["reference_value"] for pair in pair_row["pairs"]]
        comparison = [pair["comparison_value"] for pair in pair_row["pairs"]]
        if not (
            (
                all(value == 0 for value in reference)
                and all(value > 0 for value in comparison)
            )
            or (
                all(value == 0 for value in comparison)
                and all(value > 0 for value in reference)
            )
        ):
            raise ValueError("analyst_condition_restriction_not_observed")
    else:
        dominance = pair_row.get("descriptive_dominance")
        if not (isinstance(dominance, (int, float)) and dominance > 0.5):
            raise ValueError("analyst_single_unit_majority_contribution_not_observed")
    return pairing


def _analyst_findings(root, config, actions, resolved, receipts):
    """Derive all observations from source-bound query receipts, never agent numbers."""
    reports = []
    for request, entry, fitted_row in resolved:
        if "disposition" not in request:
            continue
        if request["result_sha256"] != entry["event"]["result_sha256"]:
            raise ValueError("analyst_finding_result_hash_mismatch")
        gene = fitted_row.get("gene_id", fitted_row["hypothesis_id"])
        queries = _finding_queries(root, actions, request, receipts, gene)
        observations, support = _finding_observations(request, queries, gene)
        disposition = request["disposition"]
        _check_finding_disposition(disposition, fitted_row, support)
        pairing = None
        if disposition in {
            "condition_restricted_candidate",
            "single_unit_dominance_candidate",
        }:
            pairing = _finding_unit_pairing(root, config, request, queries, gene)
        reports.append(
            {
                "target_id": request["target_id"],
                "hypothesis_id": request["hypothesis_id"],
                "gene_id": gene,
                **({"branch_id": entry["branch_id"]} if "branch_id" in entry else {}),
                "source_action": request["action"],
                "result_sha256": request["result_sha256"],
                "disposition": disposition,
                "evidence_refs": request["evidence_refs"],
                "observations": observations,
                "qualification": request["qualification"],
                "qualification_origin": "agent_selected_not_independent_review",
                "reporting_scope": (
                    "exploratory_description_no_significance_or_discovery_claim"
                ),
                **({"declared_pairing": pairing} if pairing else {}),
            }
        )
    return reports


def _base(config, final):
    version = final.get("synthesis_format", SYNTHESIS_V1)
    report = {
        "format": version,
        "method": "deterministic_receipt_projection",
        "question": config["question"],
        "assay": config["assay"],
        "execution_tier": config["execution_tier"],
        "snapshot_id": config["snapshot_id"],
        "source_sha256": config["source_sha256"],
        "outcome": final["outcome"],
        "reason": final["reason"],
        "termination_context": final.get("termination_context"),
        **({"analyst": final["analyst"]} if "analyst" in final else {}),
        "harness_accepted": final["harness_accepted"],
        "scientific_correctness": None,
        "usage": final["usage"],
        "evidence": {"status": "not_applicable"},
        "tool_events": [],
        "targets": [],
        "findings": [],
        "outputs_complete": False,
    }
    if version == SYNTHESIS_V1:
        return report
    primary_branch_id = final.get("primary_branch_id")
    if (
        version != SYNTHESIS_V2
        or not isinstance(primary_branch_id, str)
        or re.fullmatch(_ID, primary_branch_id) is None
    ):
        raise ValueError("synthesis_version_or_primary_branch_invalid")
    task = config["public_task"]
    context = task["context"]
    design = context.get("design")
    levels = design.get("condition_levels") if isinstance(design, dict) else None
    if levels is None:
        levels = context.get("condition_labels")
    if not isinstance(levels, list) or not all(
        isinstance(level, str) and len(level) <= 160 for level in levels[:20]
    ):
        levels = []
    requirements = task.get("user_parameter_requirements")
    requirement_count = len(requirements) if isinstance(requirements, list) else 0
    report.update(
        primary_branch_id=primary_branch_id,
        branches=[
            {"branch_id": primary_branch_id, "outputs_complete": False, "targets": []}
        ],
        input_declarations={
            "measurement": task["measurement"],
            "input_kind": config["input_kind"],
            "biological_unit": task["unit_hierarchy"]["population_unit_label"],
            "condition_levels": levels[:20],
            "feature_count": len(task["gene_ids"]),
        },
        requests=[
            {
                key: target[key]
                for key in (
                    "target_id",
                    "estimand",
                    "contrast",
                    "measurement_units",
                )
            }
            for target in task["questions"]
        ],
        explicit_requirement_count=requirement_count,
    )
    return report


def _source(entry, directory):
    event = entry["event"]
    return {
        "action": entry["action"],
        "tool": event["tool"],
        "receipt_id": event["receipt_id"],
        **({"branch_id": entry["branch_id"]} if "branch_id" in entry else {}),
        "observation_node_id": entry["observation_node_id"],
        "result_sha256": event["result_sha256"],
        "result_path": directory + "/" + event["receipt_id"] + ".json",
    }


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
    return {
        "target_id": target["target_id"],
        "source": _source(entry, directory),
        **(
            {
                "target_sha256": hashlib.sha256(
                    canonical_json_bytes(target),
                ).hexdigest()
            }
            if "branch_id" in entry
            else {}
        ),
        "requested": {
            key: target[key]
            for key in (
                "estimand",
                "contrast",
                "units",
                "reference_population",
                "prediction_reference",
            )
        },
        "claim_type": target["claim_type"],
        "estimate_status": target["estimate_status"],
        "evidence_scope": target["evidence_scope"],
        "computed_contrast": contrast,
        "biological_unit": payload.get("biological_unit"),
        "analysis_layer": payload.get("analysis_layer"),
        "selection_conditional_inference": payload.get(
            "selection_conditional_inference"
        ),
        "aggregation_definition": payload.get("aggregation_definition"),
        "prediction_structure": payload.get(
            "prediction_structure", "coordinate_specific"
        ),
        "limitation_codes": target.get("limitation_codes", []),
        "support_checks": target.get("support_checks", []),
        "hypothesis_family": {
            "attempted_count": len(rows),
            "tested_count": len(payload.get("multiplicity_scope", [])),
            "status_counts": counts,
            "adjustment": "Benjamini_Hochberg_over_all_attempted_hypotheses"
            if rows
            else None,
            "unavailable_policy": "internal_p_one_positions_public_p_q_remain_missing"
            if rows
            else None,
        },
    }


def _replication(entry, component):
    diagnostic = entry["diagnostics"]
    if component is None:
        units = diagnostic.get("biological_units", {})
        return {
            key: units[key]
            for key in ("analysis_unit_count", "library_count", "population_unit")
            if key in units
        }
    design = (
        diagnostic.get("cell_state_effect", {})
        .get("fits_by_type", {})
        .get(component, {})
        .get("design", {})
    )
    return {
        key: design[key]
        for key in ("independent_donors_per_condition", "paired_donor_blocking")
        if key in design
    }


def _receipt_diagnostics(result):
    value = (result or {}).get("diagnostics")
    return value if isinstance(value, dict) else {}


def _event_summary(event, result, position, directory):
    diagnostics = _receipt_diagnostics(result)
    design = diagnostics.get("design", {})
    return {
        "action": position,
        "tool": event["tool"],
        "receipt_id": event["receipt_id"],
        "status": event["status"],
        "elapsed_sec": event.get("elapsed_sec"),
        "error": event.get("error"),
        "diagnostic_status": diagnostics.get("status"),
        "design_check": {
            key: design[key]
            for key in (
                "rank",
                "column_count",
                "condition_number_validated",
                "condition_number",
                "residual_df",
            )
            if key in design
        },
        "result_path": directory + "/" + event["receipt_id"] + ".json"
        if event["status"] == "succeeded"
        else None,
    }


def _branch_event_metadata(result, action, specification):
    diagnostics = _receipt_diagnostics(result)
    quality = diagnostics.get("expression_quality", {})
    method = result.get("method", {})
    return {
        "branch_id": action_branch_id(action),
        "method": {
            key: method[key]
            for key in ("tool", "version", "count_model", "continuous_model")
            if method.get(key) is not None
        },
        "decision_rationale": (action.get("decision") or {}).get("why"),
        "data_checks": {
            key: (quality if key in quality else diagnostics)[key]
            for key in (
                "observation_count",
                "gene_count",
                "finite",
                "zero_fraction",
                "constant_gene_count",
                "negative_value_count",
            )
            if key in quality or key in diagnostics
        },
        "biological_unit_checks": {
            key: diagnostics["biological_units"][key]
            for key in (
                "biological_unit_count",
                "analysis_unit_count",
                "identity_and_nesting_valid",
            )
            if key in diagnostics.get("biological_units", {})
        },
        "analysis_error": diagnostics.get("analysis_error"),
        **(
            {"specification_id": specification.specification_id}
            if specification is not None
            else {}
        ),
    }


def _verify_diagnostic_file_type(suffix, raw):
    if suffix in _PLOT_SIGNATURES:
        if not raw.startswith(_PLOT_SIGNATURES[suffix]):
            raise ValueError("synthesis_diagnostic_plot_media_invalid")
    elif suffix == ".svg":
        try:
            tag = ElementTree.fromstring(raw).tag
        except ElementTree.ParseError:
            raise ValueError("synthesis_diagnostic_plot_media_invalid") from None
        if tag != "{http://www.w3.org/2000/svg}svg":
            raise ValueError("synthesis_diagnostic_plot_media_invalid")
    elif suffix == ".json":
        if not isinstance(decode_json(raw), (dict, list)):
            raise ValueError("synthesis_diagnostic_plot_sidecar_invalid")
    else:
        try:
            rows = csv.reader(io.StringIO(raw.decode("utf-8")), strict=True)
            if not next(rows, []):
                raise ValueError("synthesis_diagnostic_plot_sidecar_invalid")
        except (csv.Error, UnicodeDecodeError):
            raise ValueError("synthesis_diagnostic_plot_sidecar_invalid") from None


def _verified_diagnostic_file(root, stage, artifacts, name, suffixes):
    if not isinstance(name, str) or not 0 < len(name) <= 320:
        raise ValueError("synthesis_diagnostic_plot_path_invalid")
    relative = Path(name)
    if (
        relative.as_posix() != name
        or relative.suffix not in suffixes
        or not all(re.fullmatch(_ID, part) for part in relative.parts)
    ):
        raise ValueError("synthesis_diagnostic_plot_path_invalid")
    expected = artifacts.get(name)
    if expected is None:
        raise ValueError("synthesis_diagnostic_plot_not_checkpoint_bound")
    path = stage / relative
    raw = _read(root / path, 16_000_000)
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("synthesis_diagnostic_plot_artifact_changed")
    _verify_diagnostic_file_type(relative.suffix, raw)
    return {"path": path.as_posix(), "sha256": expected}


def _verified_diagnostic_plots(root, position, result, artifacts):
    plots = _receipt_diagnostics(result).get("diagnostic_plots", [])
    if not isinstance(plots, list) or len(plots) > 20:
        raise ValueError("synthesis_diagnostic_plot_list_invalid")
    stage = Path("checkpoints") / f"action-{position:04d}"
    suffixes = {"plot": {".png", ".svg", ".pdf"}, "numeric_sidecar": {".csv", ".json"}}
    verified = []
    for pair in plots:
        if not isinstance(pair, dict) or pair.keys() != suffixes.keys():
            raise ValueError("synthesis_diagnostic_plot_pair_invalid")
        verified.append(
            {
                role: _verified_diagnostic_file(
                    root, stage, artifacts, pair[role], allowed
                )
                for role, allowed in suffixes.items()
            }
        )
    return verified


def verify_analyst_records(root, config, analyst, actions, node_ids):
    """Rebuild query values and bind prospective decisions to their actual context."""
    from .checkpoints import CheckpointStore, digest

    root = Path(root)
    runtime = _json(root / "runtime.json")
    store = CheckpointStore(
        root / "checkpoints",
        {
            "config": digest(config),
            "snapshot": config["snapshot_id"],
            "source": config["source_sha256"],
            "python": runtime["runtime"]["python"],
            "packages": runtime["runtime"]["all_package_versions"],
        },
    )
    bindings = _observation_bindings(root, node_ids)
    snapshot = _json(root / "snapshot.json")
    nodes = [
        _json(root / "audit" / "nodes" / (node + ".json"), 4_000_000)
        for node in node_ids
    ]
    queries = _verified_analyst_queries(
        root,
        config,
        actions,
        bindings,
        snapshot,
        store,
        nodes,
    )
    if analyst.get("query_receipts") != queries:
        raise ValueError("analyst_query_journal_mismatch")
    _verify_analyst_decisions(root, config, analyst, nodes)
    return True


def _analyst_query_binding(nodes, query_nodes, number, action):
    action_nodes = [
        (index, node)
        for index, node in enumerate(nodes)
        if node["kind"] == "episode.action"
        and node["payload"].get("sequence") == number
    ]
    if len(action_nodes) != 1 or action_nodes[0][1]["payload"].get("action") != action:
        raise ValueError("analyst_query_action_binding_mismatch")
    following = nodes[action_nodes[0][0] + 1 :]
    boundary = next(
        (
            index
            for index, node in enumerate(following)
            if node["kind"] == "episode.action"
        ),
        len(following),
    )
    rejections = [
        node["payload"]
        for node in following[:boundary]
        if node["kind"] == "episode.action_rejected"
    ]
    matches = [item for item in query_nodes if item["sequence"] == number]
    # Rejected queries have a recorded rejection instead of a successful receipt.
    if not matches:
        if (
            len(rejections) == 1
            and rejections[0].get("status") == "rejected"
            and rejections[0].get("steps") == number
            and rejections[0].get("state") == "needs_revision"
            and rejections[0]
            .get("diagnostic", "")
            .startswith("evidence_query_rejected:")
        ):
            return None
        raise ValueError("analyst_query_receipt_missing")
    if len(matches) != 1 or rejections:
        raise ValueError("analyst_query_receipt_duplicated")
    return matches[0]


def _verified_analyst_queries(root, config, actions, bindings, snapshot, store, nodes):
    query_nodes = [
        node["payload"] for node in nodes if node["kind"] == "episode.evidence_query"
    ]
    expected = []
    for number, action in enumerate(actions, 1):
        if action.get("kind") != "query_evidence":
            continue
        query_binding = _analyst_query_binding(nodes, query_nodes, number, action)
        if query_binding is None:
            continue
        source = action["source_action"]
        if (
            type(source) is not int
            or not 1 <= source < number
            or actions[source - 1].get("kind") != "tool"
        ):
            raise ValueError("analyst_query_source_invalid")
        expected.append(
            _verified_analyst_query(
                root,
                config,
                snapshot,
                store,
                action,
                number,
                source,
                bindings.get(source, {}),
                query_binding,
            )
        )
    return expected


def _verified_analyst_query(
    root,
    config,
    snapshot,
    store,
    action,
    number,
    source,
    source_binding,
    query_binding,
):
    from .checkpoints import digest, sha256
    from .episode_evidence_queries import query_result, rebuild_query_page

    source_stage, query_stage = f"action-{source:04d}", f"query-{number:04d}"
    source_file = root / "checkpoints" / source_stage / "observation.json"
    query_file = root / "checkpoints" / query_stage / "query-observation.json"
    if store.verify(query_stage, (source_stage,)) is None:
        raise ValueError("analyst_query_checkpoint_missing")
    retained = _json(query_file, 128_000)
    receipt = retained["query_receipt"]
    source_response = _json(source_file, 128_000_000)
    if (
        receipt.get("source_action") != source
        or receipt.get("action") != number
        or receipt.get("snapshot_id") != config["snapshot_id"]
        or receipt.get("query_sha256") != digest(action["query"])
        or receipt.get("source", {}).get("checkpoint_sha256") != sha256(source_file)
        or receipt["source"].get("checkpoint_receipt_sha256")
        != sha256(source_file.parent / "receipt.json")
        or source_binding.get("result_sha256") != sha256(source_file)
        or query_binding.get("query_receipt") != receipt
        or query_binding.get("response_sha256") != sha256(query_file)
        or query_binding.get("checkpoint_receipt", {}).get("sha256")
        != sha256(query_file.parent / "receipt.json")
    ):
        raise ValueError("analyst_query_binding_mismatch")
    inputs = {
        "measurement": config["public_task"]["measurement"],
        "gene_ids": config["public_task"]["gene_ids"],
        "public_task": config["public_task"],
        "public_task_sha256": digest(config["public_task"]),
        "sample_id_column": config["public_task"].get(
            "observation_id_label", "sample_id"
        ),
    }
    if config["public_task"].get("sample_ids"):
        inputs["sample_ids"] = config["public_task"]["sample_ids"]
    for role, prefix in (
        ("expression", "counts"),
        ("metadata", "metadata"),
        ("annotations", "annotation"),
    ):
        inputs[prefix + "_path"] = str(root / snapshot[role]["path"])
        inputs[prefix + "_sha256"] = snapshot[role]["sha256"]
    page_receipt = retained["query_result"].get("query_receipt")
    if page_receipt is None:
        rebuilt = query_result(
            source_response["result"], action["query"], bindings=inputs
        )
    else:
        rebuilt = rebuild_query_page(
            source_response["result"],
            action["query"],
            bindings=inputs,
            receipt=page_receipt,
        )
    if (
        receipt.get("response_sha256") != digest(rebuilt)
        or retained["query_result"] != rebuilt
    ):
        raise ValueError("analyst_query_response_changed")
    if len(canonical_json_bytes(retained)) > 12 * 1024:
        raise ValueError("analyst_complete_query_response_exceeds_bound")
    return receipt


def _verify_analyst_decisions(root, config, analyst, nodes):
    from .checkpoints import digest, sha256

    pre_nodes = [
        node["payload"] for node in nodes if node["kind"] == "episode.analyst_decision"
    ]
    outcome_nodes = [
        node["payload"]
        for node in nodes
        if node["kind"] == "episode.analyst_decision_outcome"
    ]
    expected_decisions = []
    for pre_node in pre_nodes:
        number = pre_node["selection"]
        folder = root / "agent" / "decisions"
        pre_path, post_path = (
            folder / f"selection-{number:04d}.json",
            folder / f"outcome-{number:04d}.json",
        )
        pre, post = _json(pre_path), _json(post_path)
        context = _json(root / "agent" / f"selection-{number:04d}" / "context.json")
        matched = [item for item in outcome_nodes if item["selection"] == number]
        if (
            pre_node["artifact"]["sha256"] != sha256(pre_path)
            or pre.get("context_sha256") != digest(context)
            or pre_node.get("context_sha256") != digest(context)
            or pre.get("snapshot_id") != config["snapshot_id"]
            or len(matched) != 1
            or matched[0]["artifact"]["sha256"] != sha256(post_path)
            or post.get("pre_action_sha256") != sha256(pre_path)
        ):
            raise ValueError("analyst_decision_binding_mismatch")
        position = next(
            index
            for index, node in enumerate(nodes)
            if node["kind"] == "episode.analyst_decision"
            and node["payload"]["selection"] == number
        )
        following = nodes[position + 1 :]
        first_action = next(
            (node["payload"] for node in following if node["kind"] == "episode.action"),
            None,
        )
        # An operational pre-dispatch stop may legitimately have no action.
        if first_action is not None and first_action["action"] != pre["action"]:
            raise ValueError("analyst_decision_action_mismatch")
        expected_decisions.append(
            {
                "selection": number,
                "pre_action_sha256": sha256(pre_path),
                "outcome_sha256": sha256(post_path),
            }
        )
    if analyst.get("decision_records") != expected_decisions:
        raise ValueError("analyst_decision_journal_mismatch")


def build_erql_family_synthesis(allocation, evidence=(), *, shared_holds=None):
    """Compose complete T07 candidate families under a committed allocation."""
    from .scientific_publication import family_accounting

    return family_accounting(allocation, evidence, shared_holds=shared_holds)


def build_synthesis(root, config, final, actions, *, allow_evidence_read=False):
    if allow_evidence_read is not True:
        raise PermissionError(
            "Synthesis requires coordinator evidence-reading authorization"
        )
    root = Path(root)
    report = _base(config, final)
    branch_aware = report["format"] == SYNTHESIS_V2
    if "analyst" in final:
        verify_analyst_records(
            root,
            config,
            final["analyst"],
            actions,
            final.get("audit_prefix_node_ids", []),
        )
    analyst = "analyst" in final
    requests = validate_finding_requests(
        final.get("finding_requests", []),
        analyst=analyst,
        branch_aware=branch_aware,
    )
    if analyst and final["analyst"].get("findings", []) != [
        item for item in requests if "disposition" in item
    ]:
        raise ValueError("analyst_findings_not_bound_to_terminal_requests")
    if requests and (
        not actions
        or actions[-1].get("kind") != "finish"
        or actions[-1].get("findings") != requests
    ):
        raise ValueError("synthesis_requests_not_bound_to_terminal_action")
    receipt, directory = final.get("broker_receipt"), final.get("broker_directory")
    if receipt is None and directory is None:
        if requests or final["outcome"] == "completed":
            raise ValueError("synthesis_requires_tool_receipts")
        return report
    evidence = _verified_broker_evidence(root, config, receipt, directory)
    _project_broker_evidence(
        root,
        config,
        final,
        actions,
        report,
        requests,
        evidence,
        directory,
    )
    if len(canonical_json_bytes(report)) > MAX_REPORT_BYTES:
        raise ValueError("synthesis_exceeds_report_byte_limit")
    return report


def _verified_broker_evidence(root, config, receipt, directory):
    if (
        not isinstance(directory, str)
        or re.fullmatch(r"tool-evidence(?:-resume-[0-9]{4})?", directory) is None
    ):
        raise ValueError("synthesis_broker_directory_unavailable_or_invalid")
    from devbench.study.broker import BrokerPolicy

    from .episodes import episode_evidence_limits

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
    evidence = verify_receipts(
        folder,
        expected_receipt=receipt,
        expected_input_sha256=config["snapshot_id"],
        expected_implementation_sha256=config["source_sha256"],
        allow_evidence_read=True,
        max_result_bytes=episode_evidence_limits(config)["max_result_bytes"],
        max_evidence_bytes=byte_limit,
    )
    if evidence.execution_scoping or evidence.condition != "enforced":
        raise ValueError("synthesis_broker_scope_not_supported")
    return evidence


def _project_broker_evidence(
    root,
    config,
    final,
    actions,
    report,
    requests,
    evidence,
    directory,
):
    branch_aware = report["format"] == SYNTHESIS_V2
    primary_branch_id = report.get("primary_branch_id", PRIMARY_BRANCH_ID)
    analyst = "analyst" in final
    catalog, positions, specifications, artifacts = _catalog(
        root,
        config,
        actions,
        evidence.events,
        evidence.results,
        final.get("audit_prefix_node_ids"),
        branch_aware=branch_aware,
    )
    report["evidence"] = {
        "status": "verified",
        "root_receipt": evidence.root_receipt,
        "scope": "recorded_execution_consistency_not_biological_correctness",
    }
    branches = {primary_branch_id: []}
    for event in evidence.events:
        receipt_id = event["receipt_id"]
        result, position = evidence.results.get(receipt_id), positions.get(receipt_id)
        summary = _event_summary(event, result, position, directory)
        if branch_aware and position is not None:
            summary.update(
                _branch_event_metadata(
                    result or {},
                    actions[position - 1],
                    specifications.get(receipt_id),
                )
            )
            branches.setdefault(summary["branch_id"], [])
            if receipt_id in artifacts:
                plots = _verified_diagnostic_plots(
                    root,
                    position,
                    result,
                    artifacts[receipt_id],
                )
                if plots:
                    summary["diagnostic_plots"] = plots
        report["tool_events"].append(summary)
    for (branch_id, _), entry in catalog.items():
        branches[branch_id].append(_target_summary(entry, directory))
    report["targets"] = branches[primary_branch_id]
    resolved = _resolve(requests, catalog, analyst=analyst, branch_aware=branch_aware)
    for request, entry, row in resolved:
        if "disposition" in request:
            continue
        report["findings"].append(
            {
                "target_id": request["target_id"],
                "source": _source(entry, directory),
                "estimate": row,
                "replication": _replication(entry, row.get("component_id")),
            }
        )
    if analyst:
        report["analyst_findings"] = _analyst_findings(
            root, config, actions, resolved, final["analyst"]["query_receipts"]
        )
    report["outputs_complete"] = branch_outputs_complete(
        config,
        catalog,
        primary_branch_id,
    )
    if branch_aware:
        report["branches"] = [
            {
                "branch_id": branch_id,
                "targets": targets,
                "outputs_complete": branch_outputs_complete(config, catalog, branch_id),
            }
            for branch_id, targets in branches.items()
        ]


def safe_synthesis(root, config, final, actions):
    """Evidence failure withholds every numerical claim; callers deny acceptance."""
    try:
        return build_synthesis(root, config, final, actions, allow_evidence_read=True)
    except (OSError, ValueError, TypeError, KeyError) as error:
        report = _base(config, final)
        reason = str(error).replace(str(root), "<episode>")[:2000]
        report["evidence"] = {
            "status": "withheld",
            "error_type": type(error).__name__,
            "reason": reason,
        }
        return report


def public_episode_synthesis(report, *, guard=None, descriptor=None, publication=None):
    """Bounded public report from already verified retained synthesis."""
    from .scientific_publication import public_metadata, safe_diagnostic

    admitted = None
    if publication is not None:
        from .scientific_publication import RenderedPublication

        if type(publication) is not RenderedPublication:
            raise PermissionError("publication_requires_bound_renderer")
        with publication.registry.ledger._database() as db:
            db.execute("BEGIN")
            publication.guard(db)
            admitted = publication.registry.projection(
                publication.subject_id, delivery=publication.delivery, connection=db
            )

    report = (
        guard.project(report, descriptor=descriptor)
        if guard is not None
        else public_metadata(report, descriptor=descriptor)
    )
    targets = []
    for row in report.get("targets", []):
        if not isinstance(row, dict):
            continue
        target_id = row.get("target_id")
        family = row.get("hypothesis_family")
        targets.append(
            {
                "target_id": target_id
                if isinstance(target_id, str) and re.fullmatch(_ID, target_id)
                else None,
                "estimate_status": row["estimate_status"]
                if row.get("estimate_status")
                in {"estimated", "not_estimable", "unsupported", "withheld", "failed"}
                else "withheld",
                "hypothesis_family": {
                    key: family[key]
                    for key in ("attempted_count", "tested_count")
                    if isinstance(family, dict)
                    and type(family.get(key)) is int
                    and family[key] >= 0
                },
            }
        )
    outcome = report.get("outcome")
    return {
        "format": "devbench.episode-publication/1",
        "outcome": outcome
        if outcome in {"completed", "incomplete", "refused", "failed", "cancelled"}
        else "withheld",
        "reason": safe_diagnostic(report.get("reason"))
        if report.get("reason")
        else None,
        "scientific_correctness": None,
        "targets": targets,
        "outputs_complete": report.get("outputs_complete") is True,
        "interpretation": admitted.get("interpretation")
        if admitted is not None
        else None,
        **({"publication": admitted} if admitted is not None else {}),
    }


def _text(value):
    escaped = html.escape(str(value), quote=False).replace("\n", " ").replace("\r", " ")
    return re.sub(r"([\\`*_{}\[\]()#+|>~])", r"\\\1", escaped)


def _render_scientific_questions(report):
    declared = report["input_declarations"]
    lines = [
        "# RNA-seq analysis episode",
        "",
        "## What data do I have?",
        "",
        f"The supplied declaration specifies {_text(declared['input_kind'])} with "
        f"{_text(declared['measurement'])} measurements, "
        f"{declared['feature_count']} requested features "
        f"and {_text(declared['biological_unit'])} as the biological unit.",
        "",
    ]
    if declared["condition_levels"]:
        lines += [
            "Supplied condition levels are "
            + _text(declared["condition_levels"])
            + ".",
            "",
        ]
    checks = next(
        (
            event["data_checks"]
            for event in report["tool_events"]
            if event["tool"] == "audit_expression_quality" and event.get("data_checks")
        ),
        {},
    )
    units = next(
        (
            event["biological_unit_checks"]
            for event in report["tool_events"]
            if event["tool"] == "validate_biological_units"
            and event.get("biological_unit_checks")
        ),
        {},
    )
    for observations, name in ((checks, "input quality"), (units, "biological units")):
        if observations:
            lines += [
                "Recorded observations on "
                + name
                + " include "
                + ", ".join(
                    _text(key.replace("_", " ")) + " = " + _text(value)
                    for key, value in observations.items()
                )
                + ".",
                "",
            ]
        else:
            lines += ["Verified observations on " + name + " are unavailable.", ""]
    lines += ["## What question am I investigating?", "", _text(report["question"]), ""]
    for request in report["requests"]:
        lines += [
            "Requested wording for "
            + _text(request["target_id"])
            + " is "
            + _text(request["estimand"])
            + " with contrast "
            + _text(request["contrast"])
            + " in "
            + _text(request["measurement_units"])
            + ".",
            "",
        ]
    lines += [
        f"The public task contains {report['explicit_requirement_count']} explicit "
        "parameter requirement records. "
        "[Provenance manifest](provenance_manifest.json)",
        "",
        "## Which methods are appropriate, and why?",
        "",
    ]
    fits = [
        event
        for event in report["tool_events"]
        if event["tool"] in _PRODUCERS.values() and event.get("method")
    ]
    for event in fits:
        method = event["method"]
        model = method.get("count_model") or method.get("continuous_model")
        lines += [
            f"Action {event['action']} in branch {_text(event['branch_id'])} recorded "
            + _text(method.get("tool", event["tool"]))
            + (f" ({_text(method['version'])})" if "version" in method else "")
            + (f" using {_text(model)}" if model else "")
            + ".",
            "",
        ]
        if event["decision_rationale"]:
            lines += [
                "The recorded decision gave this rationale. "
                + _text(event["decision_rationale"]),
                "",
            ]
    if not fits:
        lines += ["No fitted procedure was verified.", ""]
    lines += [
        "This episode provides evidence about the recorded procedures and "
        "their design diagnostics. "
        "Comparative method qualification requires independent evidence.",
        "",
        "## What happened?",
        "",
        f"The episode outcome is {_text(report['outcome'])} with reason "
        f"{_text(report['reason'])}. The execution tier is "
        f"{_text(report['execution_tier'])} and receipt evidence is "
        f"{_text(report['evidence']['status'])}.",
        "",
    ]
    return lines


def _render_diagnostic_plots(events):
    lines = []
    for event in events:
        for entry in event.get("diagnostic_plots", []):
            lines += [
                f"Action {event['action']} retained a receipt-verified diagnostic "
                f"figure and its numeric data. [Plot]({entry['plot']['path']}) "
                f"[Numeric sidecar]({entry['numeric_sidecar']['path']})",
                "",
            ]
    return lines or [
        "No verified diagnostic plots or numeric sidecars were retained.",
        "",
    ]


def _render_analyst(report, subheading, branch_aware):
    lines = []
    analyst = report["analyst"]
    lines += [
        subheading + "Analyst decisions and unresolved concerns",
        "",
        "Disposition: "
        + _text(analyst["disposition"])
        + ". "
        + _text(analyst["stopping_reason"]),
        "",
        "Recorded prospective decisions: "
        + str(len(analyst["decision_records"]))
        + "; read-only evidence queries: "
        + str(len(analyst["query_receipts"]))
        + ".",
        "",
    ]
    for proposal in analyst.get("followup_proposals", []):
        lines += [
            "Unexecuted proposal ("
            + _text(proposal["status"])
            + "): "
            + _text(proposal["question"]),
            "Proposed procedure: "
            + _text(proposal["candidate_procedure"])
            + "; inputs/contrast: "
            + _text(proposal["inputs_or_contrast"])
            + "; desired evidence: "
            + _text(proposal["desired_evidence"]),
            "",
        ]
    for finding in report.get("analyst_findings", []):
        branch = " in branch " + _text(finding["branch_id"]) if branch_aware else ""
        lines += [
            "Gene "
            + _text(finding["gene_id"])
            + branch
            + ": "
            + _text(finding["disposition"])
            + ".",
            "Agent-selected qualification: " + _text(finding["qualification"]),
            "Scope: exploratory description; no significance or discovery claim.",
        ]
        for observed in finding["observations"]:
            lines.append(
                "Receipt field "
                + _text(observed["field_ref"])
                + ": "
                + _text(observed["value"])
                + "."
            )
        lines.append("")
    return lines


def _render_targets(report, subheading, branch_aware):
    lines = []
    targets = report["targets"]
    if branch_aware:
        targets = [
            target for branch in report["branches"] for target in branch["targets"]
        ]
    for target in targets:
        branch = _text(target["source"]["branch_id"]) + " / " if branch_aware else ""
        lines += [
            subheading + branch + _text(target["target_id"]),
            "",
            "Requested wording (not a computed quantity): "
            + _text(target["requested"]["contrast"])
            + "; requested units: "
            + _text(target["requested"]["units"]),
            "",
        ]
        contrast = target["computed_contrast"]
        if contrast:
            from devbench.study.biology_answers import ComputedConditionContrast

            computed = ComputedConditionContrast.model_validate_json(
                canonical_json_bytes(contrast)
            )
            lines += [
                "Computed contrast: " + _text(computed.describe()),
                "",
                "Biological unit: "
                + _text(target["biological_unit"])
                + "; evidence scope: "
                + _text(target["evidence_scope"]),
                "",
            ]
        family = target["hypothesis_family"]
        lines += [
            f"Attempted hypotheses: {family['attempted_count']}; "
            f"reported tests: {family['tested_count']}. "
            + "Status counts: "
            + ", ".join(
                _text(status) + ": " + str(count)
                for status, count in family["status_counts"].items()
            ),
            "",
        ]
        if family["adjustment"]:
            lines += [
                "BH adjustment includes all attempted hypotheses; unavailable "
                "positions retain internal p=1, with no fabricated public p/q values. "
                "Highlight selection does not redefine this family.",
                "",
            ]
        lines += [
            "Conditional inference flag: "
            + _text(target["selection_conditional_inference"])
            + ". "
            "Intervals and tests retain the fitted model's assumptions; "
            "empirical calibration is not established here.",
            "",
            "Limitations supplied by the tool: "
            + "; ".join(
                _text(code.replace("_", " ")) for code in target["limitation_codes"]
            ),
            "",
            "[Full recorded numerical result](" + target["source"]["result_path"] + ")",
            "",
        ]
        if target["aggregation_definition"]:
            lines += ["Aggregation: " + _text(target["aggregation_definition"]), ""]
        for check in target["support_checks"]:
            lines += [
                "Support check: "
                + _text(check["code"])
                + " — "
                + _text(check["status"])
                + ". "
                + _text(check["explanation"]),
                "",
            ]
    return lines


def _render_selected_observations(report, subheading, branch_aware):
    lines = []
    lines += [subheading + "Selected observations", ""]
    if not report["findings"]:
        lines += ["No individual hypotheses were selected for highlighting.", ""]
    for finding in report["findings"]:
        row = finding["estimate"]
        lines += [
            "- "
            + _text(row["hypothesis_id"])
            + " ("
            + _text(row.get("gene_id"))
            + ", "
            + _text(row.get("component_id"))
            + "): "
            + _text(row["estimate_status"])
        ]
        if row["estimate_status"] == "estimated":
            for index, value in enumerate(row.get("values", [])):
                interval = (
                    row.get("intervals", [])[index]
                    if index < len(row.get("intervals", []))
                    else None
                )
                text = (
                    "  "
                    + _text(row["coordinate_ids"][index])
                    + f": estimate={value:.8g}"
                )
                if interval:
                    text += (
                        f"; {interval['confidence_level'] * 100:g}% interval "
                        f"[{interval['lower']:.8g}, {interval['upper']:.8g}]"
                    )
                lines.append(text + ".")
            lines.append(
                "  p="
                + (
                    str(row["p_value"])
                    if row.get("p_value") is not None
                    else "unavailable"
                )
                + "; q="
                + (
                    str(row["q_value"])
                    if row.get("q_value") is not None
                    else "unavailable"
                )
                + "."
            )
        else:
            lines.append(
                "  No estimate or test reported: " + _text(row.get("missing_reason"))
            )
        if finding["replication"]:
            lines.append(
                "  Recorded replication: " + _text(finding["replication"]) + "."
            )
        source = finding["source"]
        branch = " in branch " + _text(source["branch_id"]) if branch_aware else ""
        lines += [
            "  Action "
            + str(finding["source"]["action"])
            + branch
            + "; receipt `"
            + finding["source"]["receipt_id"]
            + "`.",
            "",
        ]
    return lines


def _render_execution(report, subheading, branch_aware):
    lines = []
    lines += [
        subheading + "Execution and limitations",
        "",
        "Tool events: " + str(len(report["tool_events"])) + ". "
        "Failed tool events: "
        + str(sum(x["status"] != "succeeded" for x in report["tool_events"]))
        + ".",
        "",
    ]
    for event in report["tool_events"]:
        lines += [
            "- Action "
            + _text(event["action"])
            + ": "
            + _text(event["tool"])
            + "; execution "
            + _text(event["status"])
        ]
        if event["diagnostic_status"] is not None or event["design_check"]:
            lines.append(
                "  Recorded diagnostics: "
                + _text(event["diagnostic_status"])
                + "; design: "
                + _text(event["design_check"])
            )
        if event["error"]:
            lines.append("  Recorded tool error: " + _text(event["error"]))
        if branch_aware and event.get("analysis_error"):
            lines.append(
                "  Recorded analysis failure " + _text(event["analysis_error"]) + ".",
            )
        if event["result_path"]:
            lines.append("  [Result and diagnostics](" + event["result_path"] + ")")
        lines.append("")
    usage = report["usage"]
    for key, label in (
        ("elapsed_sec", "Recorded elapsed seconds"),
        ("RSS_MB", "Sampled process-tree RSS in MiB"),
        ("memory_coverage", "Memory observation coverage"),
        ("model", "Observed model"),
        ("tokens", "Observable model tokens"),
        ("cost", "Model cost"),
    ):
        lines += [
            label
            + ": "
            + (_text(usage[key]) if usage.get(key) is not None else "unknown")
            + ".",
            "",
        ]
    if report["termination_context"]:
        lines += [
            "Recorded termination request before receipt verification: "
            + _text(report["termination_context"]),
            "",
        ]
    lines += [
        "Input snapshot: `"
        + report["snapshot_id"]
        + "`. Implementation: `"
        + report["source_sha256"]
        + "`.",
        "",
    ]
    return lines


def render_synthesis(report):
    branch_aware = report["format"] == SYNTHESIS_V2
    if branch_aware:
        lines = _render_scientific_questions(report)
    else:
        lines = [
            "# RNA-seq analysis episode",
            "",
            "Question supplied: " + _text(report["question"]),
            "",
            "Outcome: "
            + _text(report["outcome"])
            + ". Reason: "
            + _text(report["reason"]),
            "",
            "Execution tier: " + _text(report["execution_tier"]),
            "",
            "Receipt evidence: " + _text(report["evidence"]["status"]),
            "",
            "Scientific correctness has not been scored against independent "
            "references. This report does not establish clinical validation, direct "
            "causality or harness benefit.",
            "",
        ]
    subheading = "### " if branch_aware else "## "
    if "analyst" in report:
        lines += _render_analyst(report, subheading, branch_aware)
    if report["evidence"]["status"] == "withheld":
        lines += [
            "Numerical statements withheld: " + _text(report["evidence"]["reason"]),
            "",
        ]
    lines += _render_targets(report, subheading, branch_aware)
    lines += _render_selected_observations(report, subheading, branch_aware)
    lines += _render_execution(report, subheading, branch_aware)
    if branch_aware:
        family_status = "complete" if report["outputs_complete"] else "incomplete"
        lines += _render_diagnostic_plots(report["tool_events"])
        lines += [
            "## What can I conclude, and what remains uncertain?",
            "",
            f"The declared primary branch is {_text(report['primary_branch_id'])}. "
            f"Its requested output families are {family_status}.",
            "Sensitivity results retain their own receipts and scope.",
            "",
            "The externally retained root supports verification of recorded "
            "execution integrity. That verification does not validate free-form "
            "interpretation. Scientific correctness has not been scored "
            "against independent references. Clinical release requires qualified "
            "independent validation and authenticated approval.",
            "",
        ]
    else:
        lines += [
            "This is a deterministic projection of recorded outputs. It does not "
            "validate free-form interpretation or supply an independent scientific "
            "review. Nonsignificance does not establish equivalence or absence.",
            "",
        ]
    result = "\n".join(lines)
    if len(result.encode()) > MAX_REPORT_BYTES:
        raise ValueError("synthesis_markdown_exceeds_report_byte_limit")
    return result


def verify_synthesis(root, expected_root, *, allow_evidence_read=False):
    if allow_evidence_read is not True:
        raise PermissionError(
            "Report verification requires evidence-reading authorization"
        )
    from .thesis_synthesis import THESIS_RECEIPT, verify_thesis_package

    if (Path(root) / THESIS_RECEIPT).is_file():
        return verify_thesis_package(Path(root), expected_root)
    from .checkpoints import digest
    from .episodes import _sources, verify_episode

    root = Path(root)
    verify_episode(root, expected_root)
    config, final, actions = (
        _json(root / name)
        for name in ("parameters.json", "episode.json", "actions.json")
    )
    if digest(_sources()) != config["source_sha256"]:
        raise ValueError("Report verification requires its recorded source version")
    recorded = _json(root / "synthesis.json", MAX_REPORT_BYTES)
    rebuilt = safe_synthesis(root, config, final, actions)
    if recorded != rebuilt or _read(
        root / "summary.md", MAX_REPORT_BYTES
    ).decode() != render_synthesis(rebuilt):
        raise ValueError("synthesis_does_not_match_verified_result_projection")
    if rebuilt["format"] == SYNTHESIS_V2:
        from devbench.core._files import read_bounded_file

        from .episode_synthesis_artifacts import synthesis_artifacts

        for name, content in synthesis_artifacts(root, config, final, rebuilt).items():
            if read_bounded_file(root / name, len(content)) != content:
                raise ValueError(
                    "synthesis_provenance_or_replay_projection_changed:" + name,
                )
    return {
        "valid": True,
        "evidence_verified": rebuilt["evidence"]["status"] == "verified",
        "finding_count": len(rebuilt["findings"]),
        "scope": "report_consistency_not_independent_scientific_validation",
    }
