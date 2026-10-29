"""Coordinator-owned extraction from retained governed execution receipts.

Output hashes are sealed after the run and bind the immutable pre-run plan.
Agent prose, caller numeric summaries and invented run receipts have no numeric
authority. Unsupported outputs and execution failures remain observable.
"""
from __future__ import annotations

from copy import deepcopy
import importlib.metadata
from pathlib import Path
import platform
import re
import tempfile
import uuid

from devharness_core.provenance import canonical_json

from .checkpoints import digest
from .episode_proposals import _absolute, _load, _seal
from .rna_execution_inputs import (_artifact, _read_json, _read_source, _source, load_execution_inputs,
                                   validate_execution_method_binding)
from .rna_publication_metrics import MAX_TABLE_BYTES, validate_gene_table, audit_bh_family
from .rna_publication_profiles import (load_assessment_intent, load_assessment_plan,
                                      read_reference_json)

FORMAT = "devbench.rna-analysis-output-bundle/2"
MAX_RUNS = 16
MAX_AGGREGATE_BYTES = 64_000_000
_FIELDS = {"format", "assessment_plan_ref", "workflow_ref", "runs", "gene_tables", "sets", "curves", "graphs",
           "diagnostics", "operator_interventions", "extraction_provenance"}


def _identity():
    versions = {}
    for name in ("devharness", "numpy", "pandas"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return {"code_sha256": _source(Path(__file__).resolve())["sha256"],
            "runtime_sha256": digest({"python": platform.python_version(), "platform": platform.platform(), "packages": versions})}


def _plan(reference, service):
    if type(reference) is not dict or not (set(reference) == {"path", "sha256", "size_bytes"}
            or {"manifest_path", "root_hash"} <= set(reference)):
        raise ValueError("Extraction requires an immutable pre-run assessment-plan reference")
    workspace = Path(service.root).parent
    plan = load_assessment_plan(reference, workspace=workspace, service=service)
    intent = load_assessment_intent(plan["intent_ref"], workspace=workspace, service=service)
    workflow = read_reference_json(plan["workflow_ref"], workspace=workspace, service=service)
    bridge = load_execution_inputs(plan["execution_inputs_ref"], artifact_service=service)
    return plan, intent, workflow["governed_workflow"], bridge


def _workflow_matches(run, step, workflow):
    if (run["step_id"] != step["step_id"] or run["branch_id"] != step["branch_id"]
            or run["method_version_id"] != step["method_version_id"]):
        return False
    if "steps" in workflow:
        return any(item == step for item in workflow["steps"])
    contracts = workflow.get("methods", {})
    if run["method_version_id"] not in contracts:
        return False
    names = {item["name"] for item in workflow.get("candidates", []) if item.get("method_version_id") == run["method_version_id"]}
    for experiment in workflow.get("experiments", []):
        for name in names & set(experiment.get("inputs", {})):
            if (run["step_id"] == experiment.get("step_ids", {}).get(name)
                    and run["branch_id"] == experiment.get("branch_id")
                    and run["inputs"] == experiment["inputs"][name]
                    and run["parameters"] == experiment.get("parameters", {}).get(name)):
                return True
    return False


def assessment_plan_identity(assessment_plan, *, artifact_service):
    """Return this immutable reference's exact identity after deep validation."""
    _plan(assessment_plan, artifact_service)
    return assessment_plan["root_hash"] if "manifest_path" in assessment_plan else assessment_plan["sha256"]


def assessment_run_binding(assessment_plan, *, artifact_service, project_id, scientific_run_id,
                           attempt_id, dataset_version_ids, controller_id, step_id=None):
    """Prepare the existing correlation fields before governed backend dispatch.

Scientific identifiers are supplied, never inferred or fabricated. This helper
grants no approval and qualifies no execution route or authenticated timestamp.
    """
    _, _, workflow, bridge = _plan(assessment_plan, artifact_service)
    for value in (project_id, scientific_run_id, attempt_id):
        if type(value) is not str or re.fullmatch(r"[0-9a-f]{32,64}", value) is None:
            raise ValueError("Supply the actual scientific correlation identities")
    if type(controller_id) is not str or re.fullmatch(r"[0-9a-f]{32}", controller_id) is None:
        raise ValueError("Supply a preallocated controller identity")
    if (type(dataset_version_ids) is not list or not 1 <= len(dataset_version_ids) <= 32
            or any(type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None for value in dataset_version_ids)
            or len(set(dataset_version_ids)) != len(dataset_version_ids)):
        raise ValueError("Supply exact bounded scientific dataset version identities")
    if step_id is not None:
        step = artifact_service.get("step", step_id)
        if not _workflow_matches(step, step, workflow):
            raise ValueError("Execution binding step is outside the immutable assessment workflow")
        validate_execution_method_binding(bridge, step["method_version_id"], step["parameters"], artifact_service=artifact_service)
    plan_identity = assessment_plan["root_hash"] if "manifest_path" in assessment_plan else assessment_plan["sha256"]
    return {"controller_id": controller_id, "correlation": {
        "project_id": project_id, "scientific_run_id": scientific_run_id, "attempt_id": attempt_id,
        "analysis_version_id": plan_identity, "dataset_version_ids": deepcopy(dataset_version_ids)}}


def _run_start_plan_binding(run, assessment_plan, service):
    manifest_path = Path(run["evidence"]["manifest_path"])
    manifest = _read_json(manifest_path, 1_000_000)
    start_path = manifest_path.parent / "nodes" / (manifest["node_ids"][0] + ".json")
    start = _read_json(start_path, MAX_TABLE_BYTES)
    if start["kind"] != "method_started" or start["sequence"] != 1:
        raise ValueError("Run evidence lacks its actual governed pre-dispatch method_started record")
    initial = start["payload"]["run"]
    for field in ("run_id", "step_id", "branch_id", "method_version_id", "inputs", "parameters"):
        if initial[field] != run[field]:
            raise ValueError("Governed run-start method/input/parameter identity differs from the retained result")
    if initial.get("correlation") != run.get("correlation"):
        raise ValueError("Run correlation changed after governed backend dispatch")
    expected = assessment_plan["root_hash"] if "manifest_path" in assessment_plan else assessment_plan["sha256"]
    observed = initial.get("correlation", {}).get("analysis_version_id")
    return {"status": "governed_plan_identity_before_dispatch" if observed == expected else "retrospective_unbound",
            "expected_plan_identity": expected, "observed_plan_identity": observed,
            "run_start_ref": _source(start_path, MAX_TABLE_BYTES)}


def _allocated_bindings(workflow, service):
    if workflow.get("format") != "devbench.rna-trial-workflow/1":
        return {}
    from .method_planner import ScientificMethodPlanner
    state = ScientificMethodPlanner(service).get(workflow["planner_session_id"])
    correlations = state.get("execution_correlations", {})
    result = {}
    for experiment in workflow["experiments"]:
        recorded = correlations.get(experiment["id"])
        if recorded is None:
            continue
        if type(recorded) is not dict or set(recorded) != set(experiment["candidates"]):
            raise ValueError("Retained execution allocation omits or adds workflow candidates")
        for candidate, binding in recorded.items():
            if type(binding) is not dict or set(binding) != {"controller_id", "correlation"}:
                raise ValueError("Use the retained exact controller/correlation execution binding")
            result[experiment["step_ids"][candidate]] = {**deepcopy(binding),
                "planner_session_id": workflow["planner_session_id"], "experiment_id": experiment["id"], "candidate": candidate}
    return result


def _execution_binding(run, workflow, service):
    """A retained allocation match does not authenticate approval or timing."""
    binding = _allocated_bindings(workflow, service).get(run["step_id"])
    if binding is not None and (binding["controller_id"] != run["run_id"] or binding["correlation"] != run.get("correlation")):
        raise ValueError("Run is an unallocated retry or differs from its retained controller execution binding")
    return binding


def _complete_run_history(receipts, workflow, service):
    """Check only this workflow's bounded step history before selecting output."""
    step_ids = {step["step_id"] for step in workflow["steps"]} if "steps" in workflow else {
        identity for experiment in workflow["experiments"] for identity in experiment["step_ids"].values()}
    if not 1 <= len(step_ids) <= 128:
        raise ValueError("Frozen workflow step history exceeds its bounds")
    import sqlite3
    try:
        with service._db() as db:
            progress = [0]
            def bound_query():
                progress[0] += 1
                return int(progress[0] > 1000)
            db.set_progress_handler(bound_query, 1000)
            try:
                history = db.execute("SELECT id,json_extract(payload,'$.step_id') FROM records WHERE kind='run' "
                    "AND json_extract(payload,'$.step_id') IN (" + ",".join("?" for _ in step_ids) + ") ORDER BY id LIMIT ?",
                    (*sorted(step_ids), MAX_RUNS + 1)).fetchall()
            finally:
                db.set_progress_handler(None, 0)
    except sqlite3.DatabaseError as error:
        raise ValueError("Bounded workflow attempt history cannot be verified; no comparison output") from error
    if len(history) > MAX_RUNS:
        raise ValueError("Actual workflow attempt history exceeds the bounded comparison allocation")
    supplied = {item.get("run_id") if type(item) is dict else item for item in receipts}
    if {identity for identity, _ in history} - supplied:
        raise ValueError("Actual run history was omitted from the frozen workflow output family")
    if len({step_id for _, step_id in history}) != len(history):
        raise ValueError("Repeated actual attempts lack a prespecified frozen retry protocol; retain raw runs independently")


def _runs(receipts, service, workflow, bridge):
    if type(receipts) is not list or not 1 <= len(receipts) <= MAX_RUNS:
        raise ValueError("Use a bounded nonempty list of actual retained run receipts")
    _complete_run_history(receipts, workflow, service)
    identities, result = set(), []
    supplied_ids = {item.get("run_id") if type(item) is dict else item for item in receipts}
    for binding in _allocated_bindings(workflow, service).values():
        identity = binding["controller_id"]
        if identity not in supplied_ids:
            try:
                service.get("run", identity)
            except ValueError as error:
                if str(error) != "Unknown run identity":
                    raise
            else:
                raise ValueError("An allocated actual run was omitted from the frozen workflow output family")
    allowed = {ref["artifact_id"] for refs in (bridge["original_refs"], bridge["execution_refs"]) for ref in refs.values()}
    for supplied in receipts:
        identity = supplied.get("run_id") if type(supplied) is dict else supplied
        if type(identity) is not str or identity in identities:
            raise ValueError("Retained run IDs must be unique")
        run = service.get("run", identity)
        if type(supplied) is dict and supplied != run:
            raise ValueError("Caller run receipt differs from the actual retained run")
        manifest_path = _absolute(run.get("evidence", {}).get("manifest_path", "invalid"))
        if not manifest_path.is_relative_to(_absolute(service.root) / "runs" / identity):
            raise ValueError("Run execution evidence lies outside its assigned retained run")
        if run["status"] not in {"succeeded", "failed", "interrupted"} or not service.provenance(identity)["valid"]:
            raise ValueError("Run lacks valid completed retained execution evidence")
        step = service.get("step", run["step_id"])
        if (run["inputs"] != step["inputs"] or run["parameters"] != step["parameters"]
                or run["method_version_id"] != step["method_version_id"] or not _workflow_matches(run, step, workflow)):
            raise ValueError("Retained run/step is outside the frozen workflow")
        _execution_binding(run, workflow, service)
        validate_execution_method_binding(bridge, run["method_version_id"], run["parameters"], artifact_service=service)
        if set(run["inputs"].values()) - allowed:
            raise ValueError("Run inputs lack original/derived or retained upstream lineage")
        for identity in run["inputs"].values():
            _artifact(service, identity)
        for identity in run["outputs"].values():
            ref, _ = _artifact(service, identity)
            if ref["generated_by"] != run["run_id"]:
                raise ValueError("Output artifact does not belong to the actual run receipt")
        allowed.update(run["outputs"].values())
        identities.add(run["run_id"])
        result.append(run)
    return result


def _retained_source(service, value):
    encoded = canonical_json(value)
    if len(encoded) > MAX_TABLE_BYTES:
        raise ValueError("Extracted table exceeds the existing result byte bound")
    with tempfile.NamedTemporaryFile(dir=service.root, suffix=".json", delete=False) as output:
        path = Path(output.name)
        output.write(encoded)
    try:
        ref = service.import_artifact(path, artifact_type="json", actor="rna-output-coordinator")
    finally:
        path.unlink(missing_ok=True)
    return _source(service.artifact_path(ref["artifact_id"]), MAX_TABLE_BYTES)


def _temporal_table(raw, run, method):
    """A narrow registered-tool adapter; unknown scientific meanings abstain.

The original tool object retains all coefficients, model selection, dispersion,
curves, diagnostics and conditional/selection-adjusted inference fields. This
derived table does not equate an omnibus p-value with an endpoint-effect test.
"""
    from devharness_server.method_adapter import dispatcher_contract
    if type(raw) is not dict:
        raise ValueError("Registered temporal results require an actual structured tool result")
    live = dispatcher_contract("fit_temporal_trajectory")
    if method["version_id"] != live.version_id:
        raise ValueError("Temporal extraction requires the exact live registered implementation")
    requested = run["parameters"].get("gene_list")
    if type(requested) is not list or len(set(requested)) != len(requested) or type(raw.get("genes")) is not dict:
        raise ValueError("Registered temporal output lacks its actual requested testing family")
    genes = raw["genes"]
    if set(genes) - set(requested):
        raise ValueError("Temporal output contains features outside its requested family")
    fdr = raw.get("fdr_methods")
    if fdr is not None and (fdr.get("q_value") != "benjamini_hochberg" or fdr.get("padj") != "benjamini_hochberg"):
        raise ValueError("Registered temporal probability semantics changed")
    rows = []
    for identity in requested:
        actual = genes.get(identity)
        if actual is None:
            row = {"feature_id": identity, "tested": True, "status": "missing", "effect": None,
                   "p_value": None, "q_value": None, "uncertainty": None}
        else:
            status = actual.get("status")
            if status not in {"ok", "no_information", "failed"}:
                raise ValueError("Unsupported temporal feature status; preserve it as an unassessed output")
            # Gaussian endpoint differences retain expression units. Count
            # endpoint SEs/effect_scale use the link scale: no inferred unit
            # conversion or promotion of that ambiguous difference occurs.
            effect = actual.get("effect_difference") if actual.get("effect_scale") == "expression_units" and status == "ok" else None
            row = {"feature_id": identity, "tested": True, "status": status, "effect": effect,
                   "p_value": actual.get("p_value"), "q_value": actual.get("q_value", actual.get("padj")), "uncertainty": None}
            if "q_value" in actual and "padj" in actual and actual["q_value"] != actual["padj"]:
                raise ValueError("Registered temporal q_value/padj fields disagree")
        rows.append(row)
    return {"format": "devbench.rna-gene-table/2", "table_id": "temporal-" + run["run_id"],
        "alignment": {"estimand_id": None, "biological_unit": None, "contrast": None, "cohort": None,
            "processing": None, "effect_scale": "expression_units" if raw.get("family") == "gaussian" else None,
            "hypothesis": "joint temporal coefficients equal zero", "shrinkage": None},
        "family": {"family_id": digest({"method_version_id": run["method_version_id"], "gene_list": requested,
                                         "inputs": run["inputs"], "parameters": run["parameters"]}),
            "eligible_ids": requested, "complete": set(genes) == set(requested),
            "probability_semantics": {"p_value": "selection_adjusted_p" if run["parameters"].get("inference_mode") == "gaussian_selection_mc" else "conditional_p",
                                      "q_value": "benjamini_hochberg"},
            "failed_hypothesis_policy": "retain_null_use_one_for_bh_denominator"},
        "mapping": {"namespace": "original_gene_ids", "annotation_ref": None,
                    "records": [{"source_id": identity, "target_id": identity, "status": "mapped"} for identity in requested]},
        "rows": rows,
        "limitations": "Registered temporal endpoint estimates and omnibus tests have different estimands; missing contrast/cohort/unit/shrinkage definitions prevent publication effect comparison. Full original output is retained."}


def _extract(receipts, plan_reference, service, *, retained_table_sources=None):
    plan, intent, workflow, bridge = _plan(plan_reference, service)
    runs = _runs(receipts, service, workflow, bridge)
    matrix_ref = bridge["original_refs"]["matrix"]
    matrix = _read_json(service.artifact_path(matrix_ref["artifact_id"]), 100_000_000, expected=matrix_ref)
    universe = set(matrix["gene_ids"])
    required = {schema for profile in intent["profile_specs"] for schema in profile["required_output_schemas"]}
    tables, diagnostics, compact_runs, source_hashes, interventions, total = [], [], [], [], [], 0
    for run in runs:
        input_refs = {name: _artifact(service, identity)[0] for name, identity in run["inputs"].items()}
        output_refs = {name: _artifact(service, identity)[0] for name, identity in run["outputs"].items()}
        manifest_path = Path(run["evidence"]["manifest_path"])
        manifest = _read_json(manifest_path, 1_000_000)
        execution_source = _source(manifest_path.parent / "nodes" / (manifest["node_ids"][-1] + ".json"), MAX_TABLE_BYTES)
        plan_binding = _run_start_plan_binding(run, plan_reference, service)
        frozen_eligible = plan_binding["status"] == "governed_plan_identity_before_dispatch"
        compact_runs.append({"run_id": run["run_id"], "step_id": run["step_id"], "method_version_id": run["method_version_id"],
            "status": run["status"], "input_refs": input_refs, "output_refs": output_refs,
            "evidence_root": run["evidence"]["root_hash"], "error": run.get("error"), "correlation": run.get("correlation"),
            "execution_record_ref": execution_source, "execution_binding": _execution_binding(run, workflow, service),
            "assessment_plan_binding": plan_binding, "frozen_comparison_eligible": frozen_eligible})
        diagnostics.append({"run_id": run["run_id"], "status": "not_assessable",
            "reason_codes": ["authenticated_admission_not_established_by_output_custody"] + (
                [] if frozen_eligible else ["retrospective_unbound_assessment_plan"]), "source_refs": [execution_source, plan_binding["run_start_ref"]]})
        recorded = run.get("execution", {}).get("operator_interventions")
        if recorded is not None:
            if type(recorded) is not list or len(recorded) > 32:
                raise ValueError("Retained operator interventions exceed their declared bound")
            interventions.append({"run_id": run["run_id"], "record_ref": execution_source,
                                  "recorded_field": "execution.operator_interventions", "count": len(recorded)})
        if run["status"] != "succeeded":
            diagnostics.append({"run_id": run["run_id"], "status": "failed", "reason_codes": ["execution_" + run["status"]], "source_refs": []})
            continue
        method = service.method(run["method_version_id"])
        implementation = method["contract"]["implementation"]
        for port, ref in output_refs.items():
            total += ref["size_bytes"]
            if ref["size_bytes"] > MAX_TABLE_BYTES or total > MAX_AGGREGATE_BYTES:
                raise ValueError("Actual result aggregate exceeds the existing output bounds")
            source_hashes.append(ref["sha256"])
            source = {"path": str(service.artifact_path(ref["artifact_id"])), "sha256": ref["sha256"], "size_bytes": ref["size_bytes"]}
            if ref["artifact_type"] != "json":
                diagnostics.append({"run_id": run["run_id"], "status": "not_assessable", "reason_codes": ["unsupported_output_schema"], "source_refs": [source]})
                continue
            actual = _read_source(source, MAX_TABLE_BYTES, workspace=Path(service.root).parent)
            if type(actual) is dict and actual.get("format") == "devbench.rna-gene-table/2":
                table = validate_gene_table(actual)
                table_source = source
            elif implementation["kind"] == "dispatcher" and implementation["tool_name"] == "fit_temporal_trajectory":
                table = validate_gene_table(_temporal_table(actual, run, method))
                if retained_table_sources is None:
                    table_source = _retained_source(service, table)
                else:
                    table_source = retained_table_sources.get((run["run_id"], port))
                    if table_source is None or _read_source(table_source, MAX_TABLE_BYTES, workspace=Path(service.root).parent) != table:
                        raise ValueError("Derived temporal table differs from the actual retained result")
                total += table_source["size_bytes"]
                if total > MAX_AGGREGATE_BYTES:
                    raise ValueError("Actual and derived result aggregate exceeds its bound")
                diagnostics.append({"run_id": run["run_id"], "status": "not_assessable",
                    "reason_codes": ["temporal_endpoint_and_omnibus_hypothesis_differ", "unknown_effect_comparability"], "source_refs": [source, table_source]})
            else:
                diagnostics.append({"run_id": run["run_id"], "status": "not_assessable", "reason_codes": ["unsupported_output_schema"], "source_refs": [source]})
                continue
            if table["format"] not in required:
                raise ValueError("Actual table schema was not required by the frozen assessment")
            if set(table["family"]["eligible_ids"]) - universe or {row["feature_id"] for row in table["rows"]} - universe:
                raise ValueError("Actual output features lack original input gene-universe lineage")
            if table["mapping"]["annotation_ref"] is not None:
                annotation = table["mapping"]["annotation_ref"]
                if not _absolute(annotation["path"]).is_relative_to(_absolute(service.root).parent) or _source(annotation["path"], MAX_TABLE_BYTES) != annotation:
                    raise ValueError("Mapping/annotation source changed or lies outside the assigned workspace")
            tables.append({"run_id": run["run_id"], "port": port, "artifact_ref": ref, "table_ref": table_source,
                           "table_id": table["table_id"], "frozen_comparison_eligible": frozen_eligible})
            audit = audit_bh_family(table)
            diagnostics.append({"run_id": run["run_id"], "status": audit["status"], "reason_codes": audit["reason_codes"], "source_refs": [source, table_source]})
    if "devbench.rna-gene-table/2" in required and not tables:
        diagnostics.append({"run_id": None, "status": "not_assessable", "reason_codes": ["required_gene_table_absent"], "source_refs": []})
    return {"format": FORMAT, "assessment_plan_ref": deepcopy(plan_reference), "workflow_ref": plan["workflow_ref"],
        "runs": compact_runs, "gene_tables": tables, "sets": [], "curves": [], "graphs": [], "diagnostics": diagnostics,
        "operator_interventions": interventions, "extraction_provenance": {**_identity(), "source_output_sha256": source_hashes,
            "chronology_assurance": "exact_plan_identity_checked_in_governed_run_start_not_authenticated_time",
            "execution_admission": "not_established_by_output_custody"}}


def extract_comparison_outputs(run_receipts, assessment_plan, *, artifact_service):
    """Resolve actual results and create a new post-run output seal."""
    record = _extract(run_receipts, assessment_plan, artifact_service)
    root = Path(artifact_service.root) / "comparison-output-bundles" / uuid.uuid4().hex
    sealed = _seal(root, "analysis-output-bundle.json", record, "rna_actual_comparison_outputs_extracted")
    return {"record_ref": _source(root / "analysis-output-bundle.json"),
            "manifest_path": sealed["manifest_path"], "root_hash": sealed["root_hash"]}


def validate_comparison_outputs(record, *, artifact_service, assessment_plan=None):
    """Compare the sealed extraction with independently re-read actual receipts."""
    if type(record) is not dict or set(record) != _FIELDS or record["format"] != FORMAT or len(canonical_json(record)) > 1_000_000:
        raise ValueError("Use the closed bounded actual output bundle /2")
    if assessment_plan is not None and record["assessment_plan_ref"] != assessment_plan:
        raise ValueError("Output bundle differs from the frozen pre-run plan")
    if record["sets"] or record["curves"] or record["graphs"]:
        raise ValueError("Unsupported/unretained sets, curves or graphs cannot acquire numeric authority")
    expected = _extract([run["run_id"] for run in record["runs"]], record["assessment_plan_ref"], artifact_service,
        retained_table_sources={(table["run_id"], table["port"]): table["table_ref"] for table in record["gene_tables"]})
    if expected != record:
        raise ValueError("Output bundle differs from actual retained outputs, diagnostics or lineage")
    return deepcopy(record)


def load_comparison_outputs(reference, *, artifact_service):
    if type(reference) is not dict:
        raise ValueError("Use an immutable actual-output reference")
    if "manifest_path" in reference:
        if not _absolute(reference["manifest_path"]).is_relative_to(_absolute(artifact_service.root).parent):
            raise ValueError("Actual output manifest lies outside the assigned workspace")
        _, record, _ = _load(reference["manifest_path"], reference["root_hash"], "analysis-output-bundle.json")
        if "record_ref" in reference and _read_source(reference["record_ref"], workspace=_absolute(artifact_service.root).parent) != record:
            raise ValueError("Actual output source differs from its sealed record")
    else:
        record = _read_source(reference, workspace=_absolute(artifact_service.root).parent)
    return validate_comparison_outputs(record, artifact_service=artifact_service)


def load_comparison_gene_table(table_ref, *, artifact_service):
    """Read a descriptor from a validated bundle under its explicit table cap.

This accessor validates the table; numeric authority and frozen eligibility come
from the independently validated bundle, not an arbitrary caller source label.
    """
    return validate_gene_table(_read_source(table_ref, MAX_TABLE_BYTES,
                                           workspace=_absolute(artifact_service.root).parent))
