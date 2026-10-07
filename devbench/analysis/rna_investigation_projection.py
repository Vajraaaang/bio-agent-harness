"""Coordinator-owned bounded scientific views over existing execution receipts.

``verify_packet_projection`` is read-only and returns a validated copied packet,
or raises ``ValueError``. ``project_verified_outputs`` retains a compact JSON
presentation through MethodService after verifying the current runs. Replay must
use the verifier, never the retaining function. Neither path executes a method,
model, retrieval, statistical calculation, or a second hypothesis adjustment.
"""
from copy import deepcopy
import csv
from datetime import datetime
import io
import json
import math
import re
import inspect
from pathlib import Path

from devharness_core.provenance import canonical_json, _stream_digest
from .method_planner import ScientificMethodPlanner
from .methods import MethodService, _digest
from .rna_investigation_contract import (
    _pointer, _source_pointer, validate_artifact_ref, validate_investigation_packet,
    verify_artifact_bytes, canonical_scientific_field, normalize_scientific_name,
    FITTED_NUMERICAL_ALIASES, contains_quantified_fitting_value, contains_fitted_numerical_findings,
)
from .workflow_state import planning_binding


FULL_FAMILY_MAX_BYTES = 16_000_000
FULL_FAMILY_TOTAL_MAX_BYTES = 32_000_000
FULL_FAMILY_MAX_HYPOTHESES = 100_000
FULL_FAMILY_MAX_ARTIFACTS = 32
COMPACT_MAX_BYTES = 24_000
_STATISTIC_ROLES = {"result", "statistics", "gene_statistics", "fit_result", "fitted_results"}
_FAILED = {"failed", "failure", "not_estimated", "nonconverged", "non_converged", "missing"}
_ESTIMATED = {"estimated", "ok", "success", "succeeded", "no_information"}
_NUMERIC_KEYS = {"p_value", "q_value", "padj", "pvalue", "qvalue", "effect", "effect_difference"}
_FITTED_NAMES = FITTED_NUMERICAL_ALIASES | {"coefficients", "genestatistics", "fittedresults", "fitoutputs"}


def _ref(methods, identity):
    manifest = methods.get("artifact", identity)
    return validate_artifact_ref({key: manifest[key] for key in
        ("artifact_id", "sha256", "artifact_type", "size_bytes")})


def _read(methods, reference, *, max_bytes=FULL_FAMILY_MAX_BYTES):
    reference = validate_artifact_ref(reference, max_bytes=max_bytes)
    manifest = methods.get("artifact", reference["artifact_id"])
    if {key: manifest[key] for key in reference} != reference:
        raise ValueError("Scientific artifact reference differs from its protected manifest")
    path = methods.artifact_path(reference["artifact_id"])
    with path.open("rb") as stream:
        content = stream.read(max_bytes + 1)
    verify_artifact_bytes(reference, content, max_bytes=max_bytes)
    return manifest, content


def _json(content):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate scientific JSON field")
            result[key] = value
        return result

    try:
        value = json.loads(content, object_pairs_hook=unique,
            parse_constant=lambda constant: (_ for _ in ()).throw(ValueError("Nonfinite scientific JSON")))
        stack, nodes = [(value, 0)], 0
        while stack:
            item, depth = stack.pop()
            nodes += 1
            if nodes > 1_000_000 or depth > 32:
                raise ValueError("Whole-family scientific JSON exceeds its structure bound")
            if type(item) is dict:
                stack.extend((child, depth + 1) for child in item.values())
            elif type(item) is list:
                stack.extend((child, depth + 1) for child in item)
            elif type(item) is float and not math.isfinite(item):
                raise ValueError("Nonfinite scientific statistics")
        return value
    except (TypeError, OverflowError, UnicodeError, RecursionError) as error:
        raise ValueError("Invalid bounded scientific JSON") from error


def _current_artifacts(methods, state):
    inputs = set(state["context"]["dataset_summary"].get("artifact_ids", []))
    methods_used = set()
    for experiment in state["experiments"]:
        for candidate_inputs in experiment.get("inputs", {}).values():
            inputs.update(candidate_inputs.values())
        for step_id in experiment.get("step_ids", {}).values():
            step = methods.get("step", step_id)
            methods_used.add(step["method_version_id"])
    for result in state["results"]:
        identity = result.get("provenance", {}).get("run_id")
        if identity:
            run = methods.get("run", identity)
            inputs.update(run["inputs"].values())
            methods_used.add(run["method_version_id"])
    for identity in inputs:
        methods.artifact_path(identity)
    for version in methods_used:
        method = methods.method(version)
        source = _ref(methods, method["source_artifact"])
        methods.artifact_path(source["artifact_id"])
        if source["sha256"] != method["contract"]["implementation"]["source_sha256"]:
            raise ValueError("Current method source differs from its immutable contract")
        environment = method.get("environment_artifact")
        if environment is not None:
            reference = _ref(methods, environment)
            methods.artifact_path(environment)
            if reference["sha256"] != method["contract"]["environment"]["lock_sha256"]:
                raise ValueError("Current method environment differs from its immutable contract")


def _current(methods, plan, *, allow_snapshot=False, stage=None):
    if not isinstance(methods, MethodService):
        raise TypeError("Scientific projections require the protected MethodService")
    if type(plan) is not dict or type(plan.get("session_id")) is not str:
        raise ValueError("Use the exact current scientific plan")
    current = methods.get("method_plan", plan["session_id"])
    if _digest(current) != _digest(plan):
        if not allow_snapshot:
            raise ValueError("Scientific projection uses a stale or foreign planner snapshot")
        snapshot = methods.get("method_plan_snapshot", _digest(plan))
        if (_digest(snapshot) != _digest(plan) or planning_binding(current) != planning_binding(plan)
                or canonical_json(current.get("model_campaign")) != canonical_json(plan.get("model_campaign"))):
            raise ValueError("Retained scientific snapshot has changed numerical trials or data bindings")
        if stage == "pre_fit_methods":
            current_experiments = {experiment["id"]: experiment for experiment in current["experiments"]}
            if any(experiment["id"] not in current_experiments or
                   experiment["declaration_sha256"] != current_experiments[experiment["id"]]["declaration_sha256"]
                   for experiment in plan["experiments"]):
                raise ValueError("Historical pre-fit declarations changed during trial progression")
            current_results = {canonical_json(result) for result in current["results"]}
            if any(canonical_json(result) not in current_results for result in plan["results"]):
                raise ValueError("Historical pre-fit results changed during trial progression")
        elif any(canonical_json(current.get(key)) != canonical_json(plan.get(key)) for key in ("experiments", "results")):
            raise ValueError("Retained post-fit snapshot has changed numerical trials or results")
    state = current if stage == "pre_fit_methods" else plan
    if any(type(state.get(key)) is not list or len(state[key]) > 128 for key in ("experiments", "results")):
        raise ValueError("Scientific result family exceeds its bound")
    _current_artifacts(methods, state)
    # The inherited receipt verifier may resolve scalar output metrics. Apply
    # our read envelope before it can deserialize any whole-family JSON.
    experiments = {experiment["id"]: experiment for experiment in state["experiments"]}
    inspected, total = set(), 0
    for result in state["results"]:
        identity = result.get("provenance", {}).get("run_id")
        if not identity or result.get("status") not in {"success", "partial"}:
            continue
        run = methods.get("run", identity)
        metric_outputs = {source.get("output") for source in
            experiments[result["experiment_id"]].get("metric_sources", {}).values()
            if source.get("kind") not in {"runtime", "validation"}}
        for role, artifact_id in run["outputs"].items():
            if artifact_id in inspected or role not in _STATISTIC_ROLES | metric_outputs:
                continue
            inspected.add(artifact_id)
            reference = _ref(methods, artifact_id)
            if reference["artifact_type"] not in {"json", "table"}:
                continue
            total += reference["size_bytes"]
            if reference["size_bytes"] > FULL_FAMILY_MAX_BYTES or total > FULL_FAMILY_TOTAL_MAX_BYTES:
                raise ValueError("Whole-family statistical/metric output exceeds its explicit byte bound")
    ScientificMethodPlanner(methods)._verified_results(state)


def _bound_policy(methods, plan, policy, *, packet=None):
    retained = plan.get("model_campaign")
    if retained is None:
        if packet is not None:
            raise ValueError("Scientific packet identity requires an actual frozen campaign policy")
        if type(policy) is not dict:
            raise ValueError("Use an explicit coordinator projection policy")
        return deepcopy(policy), None
    else:
        from .workflow_operations import WorkflowOperationLedger
        campaign = WorkflowOperationLedger(retained["ledger_path"], readonly=True).get_campaign(retained["campaign_id"])
        if (campaign is None or campaign["session_id"] != plan["session_id"]
                or any(campaign[key] != retained[key] for key in
                       ("policy_digest", "capability_digest", "identity_digest"))):
            raise ValueError("Scientific projection campaign binding changed")
        frozen = campaign["identity"].get("investigation_policy")
        if (type(frozen) is not dict
                or _digest(frozen) != campaign["identity"].get("investigation_policy_digest")):
            raise ValueError("Scientific projection has no exact frozen policy")
        for key in ("source_digest", "runtime_digest"):
            if frozen.get(key) != campaign["capability"].get(key) or retained.get(key) != campaign["capability"].get(key):
                raise ValueError("Frozen source/runtime identity differs from actual campaign bindings")
        if policy is not None:
            if type(policy) is not dict:
                raise ValueError("Use the complete exact frozen policy")
            supplied = deepcopy(policy)
            signature = supplied.pop("policy_digest", None)
            if signature != _digest(frozen) or _digest(supplied) != signature or canonical_json(supplied) != canonical_json(frozen):
                raise ValueError("Supplied policy fields differ from the exact frozen policy body")
        question = frozen.get("question_record")
        if type(question) is not dict or question.get("objective") != plan["context"]["task"]["scientific_objective"]:
            raise ValueError("Frozen question/objective is absent or differs from the scientific plan")
        if packet is not None:
            if "stage" in frozen and packet["stage"] != frozen["stage"]:
                raise ValueError("Scientific packet stage differs from its actual frozen investigation policy")
            if packet["bindings"]["policy_digest"] != _digest(frozen) or any(
                    packet["bindings"][key] != frozen[key] for key in ("source_digest", "runtime_digest")):
                raise ValueError("Scientific packet source/runtime/policy identity differs from the frozen campaign")
            if canonical_json(packet["question_record"]) != canonical_json(question):
                raise ValueError("Scientific question objective/estimand/action/source differs from the exact frozen question")
        for result in plan["results"]:
            identity = result.get("provenance", {}).get("run_id")
            if identity:
                started = datetime.fromisoformat(methods.get("run", identity)["started_at"]).timestamp()
                if not math.isfinite(started) or started < campaign["created_at"]:
                    raise ValueError("Statistics projection policy was frozen after scientific execution")
        return deepcopy(frozen), campaign


def _selection(methods, plan, policy, *, packet_policy_digest=None):
    frozen, campaign = _bound_policy(methods, plan, policy)
    if packet_policy_digest is not None and packet_policy_digest != _digest(frozen):
        raise ValueError("Scientific packet policy binding differs from its retained campaign")
    selection = frozen.get("statistics_projection")
    if selection is None:
        return None
    if (type(selection) is not dict or set(selection) != {"rule", "max_rows", "requirement_name"}
            or selection["rule"] not in {"all", "deterministic_head", "user_requirement"}
            or type(selection["max_rows"]) is not int
            or not 1 <= selection["max_rows"] <= FULL_FAMILY_MAX_HYPOTHESES):
        raise ValueError("Use an explicit bounded statistics projection rule")
    if selection["rule"] != "user_requirement" and selection["requirement_name"] is not None:
        raise ValueError("Coordinator projections cannot invent user requirements")
    if selection["rule"] in {"deterministic_head", "user_requirement"} and campaign is None:
        raise ValueError("Bounded gene selection requires a retained policy frozen before results")
    return deepcopy(selection)


def _number(value, field):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("Available " + field + " must be an actual finite numeric statistic")
    return value


def _alias(record, names):
    allowed = {_normal_name(name) for name in names}
    matches = [key for key in record if _normal_name(key) in allowed]
    if len(matches) != len({_normal_name(key) for key in matches}):
        raise ValueError("Duplicate canonical scientific statistic aliases")
    values = [record[key] for key in matches]
    if len(values) > 1 and any(canonical_json(value) != canonical_json(values[0]) for value in values[1:]):
        raise ValueError("Statistical aliases disagree in the whole-family artifact")
    return values[0] if values else None


def _reason(record):
    reason = next((record[key] for key in ("reason", "failure_code", "error")
                   if type(record.get(key)) is str and record[key].strip()), None)
    diagnostics = record.get("diagnostics", [])
    if reason is None and type(diagnostics) is list:
        reason = next((item.get("message") or item.get("code") for item in diagnostics
            if type(item) is dict and (item.get("severity") == "error" or "converg" in str(item.get("code", "")))), None)
    return reason or "The receipt-bound output omits an available fitted p-value or q-value"


def _row(identity, record, *, conditional):
    if type(record) is not dict:
        raise ValueError("Whole-family gene records must be typed objects")
    p, q = _alias(record, ("p_value", "pvalue")), _alias(record, ("q_value", "qvalue", "padj"))
    effect = _alias(record, ("effect", "effect_difference"))
    status = record.get("estimate_status", record.get("status"))
    if "estimate_status" in record or "status" in record:
        if type(status) is not str or status not in _FAILED | _ESTIMATED:
            raise ValueError("Unknown or malformed typed estimate status")
    for field in ("converged", "dispersion_converged"):
        if field in record and record[field] is not None and type(record[field]) is not bool:
            raise ValueError("Fit convergence metadata must be an exact boolean or not-applicable null")
    if "converged" in record and record["converged"] is None:
        raise ValueError("Unknown convergence cannot establish an estimated success")
    missing = p is None or q is None
    failed = status in _FAILED or record.get("converged") is False or record.get("dispersion_converged") is False
    reason = _reason(record) if failed or missing else None
    nonconverged = (record.get("converged") is False or record.get("dispersion_converged") is False
        or status in {"nonconverged", "non_converged"}
        or any(token in str(reason).casefold() for token in ("converg", "dispersion_fit_failed")))
    if failed or missing:
        if p is not None or q is not None or effect is not None:
            raise ValueError("Failed or unavailable estimates must retain null statistics in the actual output")
        row = {"hypothesis_id": identity, "estimate_status": "failed" if failed else "not_estimated",
               "p_value": None, "q_value": None, "reason": reason}
    else:
        p, q = _number(p, "p-value"), _number(q, "BH q-value")
        if not 0 <= p <= q <= 1:
            raise ValueError("Actual fitted statistics require p <= BH q <= 1")
        row = {"hypothesis_id": identity, "estimate_status": "estimated", "p_value": p, "q_value": q}
        if effect is not None:
            row["effect"] = _number(effect, "effect")
    supplied_conditional = record.get("selection_conditional", conditional)
    if type(supplied_conditional) is not bool:
        raise ValueError("Selection-conditional inference must be explicitly typed")
    if conditional and supplied_conditional is False:
        raise ValueError("Outcome-selected inference cannot deny its actual selection-conditional scope")
    row["selection_conditional"] = supplied_conditional
    return row, {"failed": int(failed or missing), "nonconverged": int(nonconverged), "missing": int(missing)}


def _table(content):
    try:
        text = content.decode("utf-8")
        delimiter = "\t" if "\t" in text.split("\n", 1)[0] else ","
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError("Whole-family statistical table has invalid distinct columns")
        records, selectors, previous_line = [], [], reader.line_num
        for record in reader:
            if len(records) >= FULL_FAMILY_MAX_HYPOTHESES or None in record:
                raise ValueError("Whole-family statistical table exceeds its row or column bounds")
            converted = dict(record)
            for field in _NUMERIC_KEYS:
                if field in converted:
                    value = converted[field]
                    converted[field] = None if value in ("", "null", "NA") else _number(float(value), field)
            for field in ("selection_conditional", "converged", "dispersion_converged"):
                if field in converted:
                    if converted[field] not in {"true", "false", ""}:
                        raise ValueError("Statistical table booleans must be typed true/false")
                    converted[field] = None if converted[field] == "" else converted[field] == "true"
            records.append(converted)
            selectors.append({"start_line": previous_line + 1, "end_line": reader.line_num})
            previous_line = reader.line_num
        document = {"gene_statistics": records, "_csv_row_selectors": selectors}
        for field, target in (("q_value_method", "fdr_methods"), ("fdr_scope", "fdr_scope")):
            supplied = [row.get(field) for row in records if field in row]
            if supplied:
                if len(supplied) != len(records) or any(value != supplied[0] for value in supplied):
                    raise ValueError("Statistical table correction declarations disagree across the whole family")
                document[target] = {"q_value": supplied[0]} if target == "fdr_methods" else supplied[0]
        return document
    except (UnicodeError, TypeError, OverflowError, csv.Error) as error:
        raise ValueError("Invalid bounded scientific statistics table") from error


def _declared_input_order(methods, run):
    orders, references = [], []
    for identity in run["inputs"].values():
        reference = _ref(methods, identity)
        # A declared compact axis is eligible; reading a large matrix to invent
        # an axis or a subset is outside this projection facade.
        if reference["artifact_type"] != "json" or reference["size_bytes"] > 128_000:
            continue
        _, content = _read(methods, reference, max_bytes=128_000)
        document = _json(content)
        if type(document) is not dict:
            continue
        for field in ("gene_order", "gene_ids", "gene_names"):
            if field in document:
                orders.append(document[field])
                references.append(reference)
    if not orders:
        return None, []
    if any(canonical_json(order) != canonical_json(orders[0]) for order in orders[1:]):
        raise ValueError("Immutable input gene/data orders disagree")
    return orders[0], list({reference["artifact_id"]: reference for reference in references}.values())


def _records(document, run, methods):
    if type(document) is not dict or document.get("model_fitting_performed") is False:
        return None
    if document.get("fdr_adjustment_performed") is False:
        return None
    roots = [name for name in ("genes", "gene_statistics") if name in document]
    if not roots:
        return None
    if len(roots) != 1:
        raise ValueError("An ambiguous whole-family statistics layout requires an explicit qualified adapter")
    root = roots[0]
    pointer_root = "/" + root
    container = document[root]
    if type(container) is dict and "rows" in container:
        # A bounded presentation envelope is never admitted as the whole family.
        if "whole_family_refs" in container or container.get("coverage") == "bounded_projection":
            raise ValueError("A compact presentation cannot stand in for whole-family fitted outputs")
        container = container["rows"]
        pointer_root += "/rows"
    if type(container) is dict:
        records = dict(container)
        locations = {gene: pointer_root + "/" + gene.replace("~", "~0").replace("/", "~1") for gene in records}
    elif type(container) is list:
        records, locations = {}, {}
        for index, row in enumerate(container):
            if type(row) is not dict:
                raise ValueError("Invalid typed whole-family statistics row")
            identity = _alias(row, ("hypothesis_id", "gene_id", "gene"))
            if type(identity) is not str or not identity or identity in records:
                raise ValueError("Whole-family statistics require distinct exact gene identities")
            records[identity] = row
            locations[identity] = pointer_root + "/" + str(index)
    else:
        raise ValueError("Unsupported whole-family gene statistics representation")
    if not records or len(records) > FULL_FAMILY_MAX_HYPOTHESES:
        raise ValueError("Whole-family gene statistics exceed their hypothesis bound")
    order = run["parameters"].get("gene_list", run["parameters"].get("genes"))
    order_refs = []
    if order is None:
        order, order_refs = _declared_input_order(methods, run)
    if (type(order) is not list or any(type(identity) is not str or not identity for identity in order)
            or len(order) != len(set(order)) or set(order) != set(records)):
        raise ValueError("The whole fitted family must match its exact declared gene/data order")
    return root, [(identity, records[identity]) for identity in order], order_refs, locations


def _correction_assurance(methods, run, document, rows):
    """Check source declarations and existing BH arithmetic; never change q."""
    from devharness_server.tools_stat import bh_adjust
    metadata = document.get("fdr_methods")
    declared = None
    if metadata is not None:
        if type(metadata) is not dict:
            raise ValueError("Correction metadata must be a typed method declaration")
        names = [_normal_name(key) for key in metadata]
        if len(names) != len(set(names)):
            raise ValueError("Correction metadata has ambiguous canonical adjustment aliases")
        supplied = [metadata[key] for key in metadata if _normal_name(key) in {"qvalue", "padj"}]
        if supplied:
            if any(type(value) is not str or _normal_name(value) not in {"benjaminihochberg", "bh"} for value in supplied):
                raise ValueError("A different adjustment cannot be presented as BH q-values")
            declared = "source_reported_benjamini_hochberg"
    if declared is None:
        method = methods.method(run["method_version_id"])
        implementation = method["contract"]["implementation"]
        events = run.get("execution", {}).get("tool_events", [])
        source_file = inspect.getsourcefile(bh_adjust)
        current_source = _stream_digest(Path(source_file), max_bytes=2_000_000)[0] if source_file is not None else None
        if (implementation.get("kind") != "dispatcher" or implementation.get("tool_name") != "fit_temporal_trajectory"
                or implementation.get("source_sha256") != current_source
                or run.get("execution", {}).get("runtime") != "trusted_dispatcher"
                or not any(event.get("tool_name") == "fit_temporal_trajectory" and event.get("succeeded") is True
                    and canonical_json(event.get("result")) == canonical_json(document) for event in events)):
            raise ValueError("BH correction scope lacks explicit metadata or a qualified captured legacy fitter")
        declared = "captured_registered_temporal_fitter_bh_path"
    if type(document.get("fdr_scope")) is not str or document["fdr_scope"] not in {"requested_genes", "declared_hypothesis_family"}:
        raise ValueError("BH correction does not declare the actual whole-family scope")
    typed = [_row(gene, record, conditional=document.get("selection_criterion") != "fixed")[0] for gene, record in rows]
    expected = bh_adjust([row["p_value"] if row["p_value"] is not None else 1.0 for row in typed])
    for row, actual in zip(typed, expected):
        if row["q_value"] is not None and not math.isclose(row["q_value"], actual, rel_tol=1e-12, abs_tol=0.0):
            raise ValueError("Original BH q-values differ from the existing adjustment over the whole declared family")
    return {"method": "benjamini_hochberg", "declaration_source": declared, "arithmetic_verified": True,
        "hypothesis_family": "exact_declared_family", "failed_slots": "unit p in denominator; original failed q remains null",
        "p_value_calibration": "not_established"}


def _families(methods, plan):
    families, refs, unavailable, consumed, seen_runs = [], [], [], 0, set()
    for result in plan["results"]:
        identity = result.get("provenance", {}).get("run_id")
        if result.get("status") not in {"success", "partial"} or not identity:
            unavailable.append("A declared scientific trial has no successful fitted execution receipt")
            continue
        if identity in seen_runs:
            raise ValueError("Duplicate scientific run cannot add independent hypotheses")
        seen_runs.add(identity)
        run = methods.get("run", identity)
        found = False
        if run["status"] != "succeeded" or run["validation"].get("passed") is not True:
            unavailable.append("A run has no passed coordinator output validation")
            continue
        for role, artifact_id in run["outputs"].items():
            if role not in _STATISTIC_ROLES:
                continue
            reference = _ref(methods, artifact_id)
            if reference["artifact_type"] not in {"json", "table"}:
                continue
            if reference["size_bytes"] > FULL_FAMILY_MAX_BYTES or consumed + reference["size_bytes"] > FULL_FAMILY_TOTAL_MAX_BYTES:
                raise ValueError("Whole-family statistical output exceeds its explicit byte bound")
            manifest, content = _read(methods, reference)
            consumed += len(content)
            if manifest.get("generated_by") != identity:
                raise ValueError("Scientific whole-family artifact belongs to another or unexecuted run")
            document = _json(content) if reference["artifact_type"] == "json" else _table(content)
            records = _records(document, run, methods)
            if records is None:
                continue
            found = True
            if len(families) >= FULL_FAMILY_MAX_ARTIFACTS:
                raise ValueError("Whole-family scientific output exceeds its artifact bound")
            root, rows, order_refs, locations = records
            correction = _correction_assurance(methods, run, document, rows)
            families.append({"ref": reference, "run": run, "document": document, "root": root,
                             "records": rows, "order_refs": order_refs, "locations": locations,
                             "correction_assurance": correction})
            refs.append(reference)
        if not found:
            unavailable.append("Successful execution " + identity + " supplies no whole-family fitted statistics")
    if len({reference["artifact_id"] for reference in refs}) != len(refs):
        raise ValueError("Duplicate whole-family outputs cannot masquerade as distinct tests")
    return families, refs, unavailable


def _projection(methods, plan, families, references, unavailable, selection):
    rows, summaries = [], []
    counts = {"failed": 0, "nonconverged": 0, "missing": 0}
    qualify = len(families) > 1
    for family in families:
        run, document = family["run"], family["document"]
        # Missing selection calibration is unknown, not a claim of unconditional inference.
        conditional = document.get("selection_criterion") != "fixed"
        family_counts = dict.fromkeys(counts, 0)
        for gene, record in family["records"]:
            pointer = family["locations"][gene]
            if canonical_json(_pointer(document, pointer)) != canonical_json(record):
                raise ValueError("Source row selector identifies a different gene or statistic")
            locator = pointer
            if family["ref"]["artifact_type"] == "table":
                index = int(pointer.rsplit("/", 1)[1])
                source = document["_csv_row_selectors"][index]
                locator = "csv:L" + str(source["start_line"]) + "-L" + str(source["end_line"]) + ";row=" + str(index)
            identity = family["ref"]["artifact_id"] + "#" + locator if qualify else gene
            row, statuses = _row(identity, record, conditional=conditional)
            rows.append(row)
            for key, number in statuses.items():
                counts[key] += number
                family_counts[key] += number
        summaries.append({"artifact_ref": family["ref"], "run_id": run["run_id"],
            "execution_evidence_root": run["evidence"]["root_hash"], "tested_hypotheses": len(family["records"]),
            "failed_estimates": family_counts["failed"], "nonconverged_estimates": family_counts["nonconverged"],
            "missing_estimates": family_counts["missing"], "fdr_scope": document.get("fdr_scope"),
            "inference_scope": document.get("inference_scope"), "model_family": document.get("family"),
            "normalization_method": document.get("normalization_method"),
            "correction_assurance": family["correction_assurance"],
            "declared_parameters": run["parameters"], "declared_gene_order_refs": family["order_refs"],
            "selection_adjustment": document.get("selection_adjustment")})
    if len(rows) > FULL_FAMILY_MAX_HYPOTHESES:
        raise ValueError("The combined current hypothesis family exceeds its bound")
    if selection["rule"] == "all":
        if len(rows) > selection["max_rows"]:
            raise ValueError("Whole-family projection exceeds its frozen row bound; it cannot silently truncate")
        selected = rows
    elif selection["rule"] == "deterministic_head":
        selected = rows[:selection["max_rows"]]
    else:
        if qualify:
            raise ValueError("Direct user gene selection across distinct tested families has no qualified identity mapping")
        name = selection["requirement_name"]
        requirement = next((row for row in plan.get("user_parameter_requirements", []) if row["name"] == name), None)
        if (requirement is None or type(requirement.get("value")) is not list or not requirement["value"]
                or any(type(value) is not str for value in requirement["value"])
                or len(requirement["value"]) != len(set(requirement["value"]))
                or not isinstance(requirement.get("evidence"), str) or not requirement["evidence"].strip()
                or re.search(r"\b(assistant|fixture|default|suggestion|template)\b", requirement["evidence"], re.I)):
            raise ValueError("Gene selection requires an exact retained direct-user requirement record")
        available = {row["hypothesis_id"]: row for row in rows}
        if set(requirement["value"]) - set(available) or len(requirement["value"]) > selection["max_rows"]:
            raise ValueError("User-selected genes exceed the actual whole family or frozen row bound")
        selected = [available[identity] for identity in requirement["value"]]
    full = len(selected) == len(rows) and selection["rule"] == "all" and not unavailable
    value = {"coverage": "whole_family" if full else "bounded_projection",
        "selection": {"rule": selection["rule"], "origin": "user" if selection["rule"] == "user_requirement" else "coordinator",
                      "requirement_name": selection["requirement_name"]},
        "whole_family_refs": references, "tested_hypotheses": len(rows), "represented_hypotheses": len(selected),
        "failed_estimates": counts["failed"], "missingness": {
            "unrepresented_hypotheses": len(rows) - len(selected), "failed_estimates_retained": True}, "rows": selected}
    summary = {"total_hypotheses": len(rows), "selected_hypotheses": len(selected),
        "failed_estimates": counts["failed"], "nonconverged_estimates": counts["nonconverged"],
        "missing_estimates": counts["missing"], "unassessed_output_runs": len(unavailable), "per_run": summaries,
        "multiplicity_scope": "Original per-run adjustments retained; no combined genes/contrasts/tuning correction is asserted",
        "selection_scope": "All current fitted runs retained; selected rows are a presentation view, not a new testing family"}
    return value, summary


def _empty(references, reason):
    return {"facts": {"observed": [], "declared": [], "inferred": [], "unknown": [{
        "field": "statistics_projection_assessment", "value": None,
        "source": {"kind": "unknown", "reference": "coordinator-current-execution-receipts"},
        "evidence_refs": [], "limitation": reason}]}, "evidence_refs": references,
        "diagnostic_refs": [], "verified_artifacts": [], "assessment_available": False}


def project_verified_outputs(methods, plan, *, policy):
    """Verify shared outputs, then retain one compact presentation artifact.

    Explicitly absent fitted statistics/configuration produces unknown coverage
    with ``assessment_available=False``. Oversized/damaged/forged outputs fail
    explicitly. This is a coordinator write and cannot be used during replay.
    """
    methods._require_writable()
    _current(methods, plan)
    families, references, unavailable = _families(methods, plan)
    if not families:
        return _empty(references, "Actual verified whole-family fitted statistics are absent; biological explanation is not assessed")
    selection = _selection(methods, plan, policy)
    if selection is None:
        return _empty(references, "An admissible statistics projection rule was not frozen; selected coverage is absent")
    statistics, summary = _projection(methods, plan, families, references, unavailable, selection)
    if summary["failed_estimates"] == summary["total_hypotheses"]:
        return _empty(references, "All actual estimates are failed or unavailable; fitted biological explanation is not assessed")
    document = {"gene_statistics": statistics, "fitted_family_coverage": summary}
    if len(canonical_json(document)) > COMPACT_MAX_BYTES:
        raise ValueError("Compact scientific statistics exceed their explicit byte bound; no silent truncation")
    from .rna_investigations import retain_investigation_json
    reference = retain_investigation_json(methods, document, actor="rna-investigation-coordinator", max_bytes=COMPACT_MAX_BYTES)
    limitation = ("Verified execution and bounded statistical coverage do not establish the best method, causal proof, "
                  "independent scientific assessment or clinical validation. Validated plot pixels are absent.")
    observed = [{"field": field, "value": value,
        "source": {"kind": "coordinator_artifact", "reference": reference["artifact_id"] + "#/" + field},
        "evidence_refs": [reference], "limitation": limitation}
        for field, value in document.items()]
    unknown = [{
        "field": "visual_assessment", "value": None, "source": {"kind": "unknown", "reference": "text-only-coordinator-view"},
        "evidence_refs": [], "limitation": "Validated plot pixels were not supplied; visual assessment is absent"}]
    if unavailable:
        unknown.append({"field": "statistics_projection_assessment", "value": None,
            "source": {"kind": "unknown", "reference": "coordinator-current-execution-receipts"},
            "evidence_refs": [], "limitation": "Some current trial outputs lack fitted family statistics; coverage is limited, "
                "and no total across the missing hypotheses is known"})
    order_refs = {ref["artifact_id"]: ref for family in families for ref in family["order_refs"]}
    evidence = {ref["artifact_id"]: ref for ref in [*references, *order_refs.values(), reference]}
    return {"facts": {"observed": observed, "declared": [], "inferred": [], "unknown": unknown},
        "evidence_refs": list(evidence.values()), "diagnostic_refs": [reference],
        "verified_artifacts": [{"ref": reference, "value": document}], "assessment_available": True}


def _contains_fitted_statistics(value):
    if contains_fitted_numerical_findings(value):
        return True
    if type(value) is dict:
        return any(_normal_name(key) in _FITTED_NAMES and contains_quantified_fitting_value(child)
                   for key, child in value.items()) or any(_contains_fitted_statistics(child) for child in value.values())
    if type(value) is list:
        return any(_contains_fitted_statistics(child) for child in value)
    return False


def _normal_name(value):
    return re.sub(r"[^a-z0-9]", "", normalize_scientific_name(value))


def verify_packet_projection(methods, packet, plan, *, policy=None):
    """Return a copied validated packet only after exact receipt/family checks."""
    packet = validate_investigation_packet(packet)
    _current(methods, plan, allow_snapshot=True, stage=packet["stage"])
    if packet["parent_session_id"] != plan["session_id"] or packet["bindings"]["plan_hash"] != _digest(plan):
        raise ValueError("Scientific packet is stale or belongs to another plan")
    if packet["bindings"]["binding_digest"] != planning_binding(plan):
        raise ValueError("Scientific packet data/requirement binding differs from the retained plan")
    if packet["scope"]["data_classification"] != plan.get("data_classification", "unclassified"):
        raise ValueError("Scientific packet classification scope differs from the actual plan binding")
    if policy is not None and policy.get("policy_digest") is not None and packet["bindings"]["policy_digest"] != policy["policy_digest"]:
        raise ValueError("Scientific packet policy binding differs from the supplied frozen policy")
    if canonical_json(packet["requirement_records"]) != canonical_json(plan.get("user_parameter_requirements", [])):
        raise ValueError("Packet user requirements differ from the exact retained records")
    _bound_policy(methods, plan, policy, packet=packet)
    needed = set()
    for category in ("observed", "declared"):
        for fact in packet["facts"][category]:
            if category == "observed" or "#" in fact["source"]["reference"]:
                needed.add(_source_pointer(fact)[0])
            name = _normal_name(fact["field"].rsplit(".", 1)[-1])
            if canonical_scientific_field(fact["field"]) not in {"gene_statistics", "fitted_family_coverage"} and (
                    name in _FITTED_NAMES and contains_quantified_fitting_value(fact["value"])
                    or _contains_fitted_statistics(fact["value"])):
                raise ValueError("Fitted statistics require the verified typed gene statistics projection")
    decoded = {}
    for reference in packet["evidence_refs"]:
        manifest = methods.get("artifact", reference["artifact_id"])
        if {key: manifest[key] for key in reference} != reference:
            raise ValueError("Scientific evidence differs from its exact protected manifest")
        methods.artifact_path(reference["artifact_id"])
        if reference["artifact_id"] in needed and reference["artifact_type"] == "json":
            _, content = _read(methods, reference, max_bytes=FULL_FAMILY_MAX_BYTES)
            decoded[reference["artifact_id"]] = _json(content)
    for category in ("observed", "declared"):
        for fact in packet["facts"][category]:
            if category == "declared" and "#" not in fact["source"]["reference"]:
                continue
            identity, pointer = _source_pointer(fact)
            if identity not in decoded or canonical_json(_pointer(decoded[identity], pointer)) != canonical_json(fact["value"]):
                raise ValueError("Scientific fact differs from its exact source artifact pointer")
    facts = {canonical_scientific_field(fact["field"]): fact for fact in packet["facts"]["observed"]}
    stats = facts.get("gene_statistics")
    if stats is None:
        if "fitted_family_coverage" in facts:
            raise ValueError("Fitted family coverage requires an actual verified statistics projection")
        return packet
    if packet["stage"] != "post_fit_explanation":
        raise ValueError("Fitted statistics cannot appear before actual fitting")
    families, references, unavailable = _families(methods, plan)
    selection = _selection(methods, plan, policy, packet_policy_digest=packet["bindings"]["policy_digest"])
    if not families or selection is None:
        raise ValueError("Fitted statistics lack whole-family evidence or a frozen projection rule")
    expected, summary = _projection(methods, plan, families, references, unavailable, selection)
    if canonical_json(stats["value"]) != canonical_json(expected):
        raise ValueError("Statistics projection membership, order, values or coverage differ from actual whole-family outputs")
    if "fitted_family_coverage" in facts and canonical_json(facts["fitted_family_coverage"]["value"]) != canonical_json(summary):
        raise ValueError("Fitted family failure/missingness/selection scope differs from actual output receipts")
    return packet
