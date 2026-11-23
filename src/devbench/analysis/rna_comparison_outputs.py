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
            "plot_adapter_sha256": _source(Path(__file__).resolve().with_name("scientific_plot_sources.py"))["sha256"],
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


class _TemporalFamilyUnavailable(ValueError):
    """The retained result cannot identify a comparable testing family."""


def _genome_wide_temporal_family(raw, parameters, input_gene_ids, filtered):
    if input_gene_ids is None:
        raise _TemporalFamilyUnavailable("temporal_genome_wide_feature_axis_unbound")
    requested = parameters.get("gene_list") or []
    total = len(input_gene_ids)
    if requested:
        reported = requested
        page = {"selection": "explicit_gene_list", "offset": None, "count": len(reported),
                "next_offset": None, "input_gene_count": total}
    else:
        offset = parameters.get("gene_offset", 0)
        limit = parameters.get("gene_limit", 100)
        if type(offset) is not int or not 0 <= offset < total or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Temporal output page lies outside the bound input axis")
        reported = input_gene_ids[offset:offset + limit]
        end = offset + len(reported)
        page = {"selection": "source_order", "offset": offset, "count": len(reported),
                "next_offset": end if end < total else None, "input_gene_count": total}
    retained_page = raw.get("gene_page")
    if (retained_page != page or any(type(retained_page[key]) is not type(value) for key, value in page.items())
            or set(reported) - set(input_gene_ids)
            or set(raw["genes"]) != set(reported)):
        raise ValueError("Temporal output page differs from its bound feature selection")

    family = raw.get("fdr_family")
    counts = ("input_gene_count", "test_family_count", "filtered_gene_count", "failed_hypothesis_count")
    if (not isinstance(family, dict) or any(type(family.get(key)) is not int for key in counts)
            or family.get("scope") != "complete_submitted_feature_axis"
            or family.get("failed_hypotheses_contribute_internal_p_one") is not True):
        raise ValueError("Invalid retained temporal genome-wide adjustment family")
    tested = family["test_family_count"]
    removed = family["filtered_gene_count"]
    failed = family["failed_hypothesis_count"]
    reported_tested = len(reported) - len(filtered)
    reported_failed = sum(row["status"] == "failed" for row in raw["genes"].values())
    if (family["input_gene_count"] != total or tested + removed != total
            or not 0 <= len(filtered) <= removed or not 0 <= reported_tested <= tested
            or not reported_failed <= failed <= reported_failed + tested - reported_tested):
        raise ValueError("Retained temporal genome-wide family counts are inconsistent with the input or report")
    if len(filtered) != removed:
        raise _TemporalFamilyUnavailable("temporal_genome_wide_filter_membership_unretained")
    eligible = [identity for identity in input_gene_ids if identity not in filtered]
    return reported, eligible, reported_tested == tested


def _temporal_table(raw, run, method, *, input_gene_ids=None):
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
    if raw.get("model_fitting_performed") is False or raw.get("fdr_adjustment_performed") is False:
        raise _TemporalFamilyUnavailable("temporal_model_fitting_refused")
    parameters = run["parameters"]
    scope = parameters.get("fdr_scope", "requested_genes")
    if (scope not in {"requested_genes", "genome_wide"}
            or raw.get("fdr_scope", "requested_genes") != scope):
        raise ValueError("Temporal result adjustment scope differs from the actual dispatch")
    requested = parameters.get("gene_list", [] if scope == "genome_wide" else None)
    if (type(requested) is not list or len(requested) > 100
            or any(type(identity) is not str for identity in requested)
            or len(set(requested)) != len(requested) or type(raw.get("genes")) is not dict):
        raise ValueError("Registered temporal output lacks its actual requested testing family")
    genes = raw["genes"]
    if any(type(row) is not dict or row.get("status") not in {"ok", "no_information", "failed", "filtered"}
           for row in genes.values()):
        raise ValueError("Unsupported temporal feature status; preserve it as an unassessed output")
    filtered = {identity for identity, row in genes.items() if row["status"] == "filtered"}
    if scope == "genome_wide":
        reported, eligible, complete = _genome_wide_temporal_family(raw, parameters, input_gene_ids, filtered)
        family_parameters = {key: value for key, value in parameters.items()
                             if key not in {"gene_list", "gene_offset", "gene_limit"}}
    else:
        if set(genes) - set(requested):
            raise ValueError("Temporal output contains features outside its requested family")
        if filtered:
            filter_record = raw.get("independent_count_filter")
            if (not isinstance(filter_record, dict) or filter_record.get("filtered_gene_count") != len(filtered)
                    or filter_record.get("filtered_gene_bh_policy") != "internal_p_one_in_requested_family"):
                raise ValueError("Retained temporal filter lacks its requested-family BH policy")
        reported = eligible = requested
        complete = not filtered and set(genes) == set(requested)
        family_parameters = parameters
    fdr = raw.get("fdr_methods")
    if fdr is not None and (not isinstance(fdr, dict) or fdr.get("q_value") != "benjamini_hochberg"
                            or fdr.get("padj") != "benjamini_hochberg"):
        raise ValueError("Registered temporal probability semantics changed")
    rows = []
    for identity in reported:
        actual = genes.get(identity)
        if actual is None:
            row = {"feature_id": identity, "tested": True, "status": "missing", "effect": None,
                   "p_value": None, "q_value": None, "uncertainty": None}
        else:
            status = actual["status"]
            # Gaussian endpoint differences retain expression units. Count
            # endpoint SEs/effect_scale use the link scale: no inferred unit
            # conversion or promotion of that ambiguous difference occurs.
            effect = actual.get("effect_difference") if actual.get("effect_scale") == "expression_units" and status == "ok" else None
            row = {"feature_id": identity, "tested": status != "filtered", "status": status, "effect": effect,
                   "p_value": actual.get("p_value"), "q_value": actual.get("q_value", actual.get("padj")), "uncertainty": None}
            if "q_value" in actual and "padj" in actual and actual["q_value"] != actual["padj"]:
                raise ValueError("Registered temporal q_value/padj fields disagree")
        rows.append(row)
    limitations = ("Registered temporal endpoint estimates and omnibus tests have different estimands; "
        "missing contrast/cohort/unit/shrinkage definitions prevent publication effect comparison. Full original output is retained.")
    if scope == "genome_wide" and not complete:
        limitations += (" This report page retains its original adjusted values and the full eligible feature axis. "
                        "Independent BH recalculation requires the remaining tested rows.")
    elif filtered and scope == "requested_genes":
        limitations += (" Filtered rows remain untested and occupy internal p=1 positions in the requested-family adjustment. "
                        "The typed tested-family audit cannot certify that denominator.")
    return {"format": "devbench.rna-gene-table/2", "table_id": "temporal-" + run["run_id"],
        "alignment": {"estimand_id": None, "biological_unit": None, "contrast": None, "cohort": None,
            "processing": None, "effect_scale": "expression_units" if raw.get("family") == "gaussian" else None,
            "hypothesis": "joint temporal coefficients equal zero", "shrinkage": None},
        "family": {"family_id": digest({"method_version_id": run["method_version_id"], "gene_list": eligible,
                                         "inputs": run["inputs"], "parameters": family_parameters}),
            "eligible_ids": eligible, "complete": complete,
            "probability_semantics": {"p_value": "selection_adjusted_p" if run["parameters"].get("inference_mode") == "gaussian_selection_mc" else "conditional_p",
                                      "q_value": "benjamini_hochberg"},
            "failed_hypothesis_policy": "retain_null_use_one_for_bh_denominator"},
        "mapping": {"namespace": "original_gene_ids", "annotation_ref": None,
                    "records": [{"source_id": identity, "target_id": identity, "status": "mapped"} for identity in reported]},
        "rows": rows,
        "limitations": limitations}


_PLOT_OUTPUT_KINDS = {
    "sets": {"enrichment_dotplot", "motif_enrichment", "gsea_running", "bulk_upset_dispositions"},
    "curves": {"temporal_spline", "saved_temporal_curve", "temporal_model_comparison", "resource_curve"},
    "graphs": {"lineage_schematic", "regulatory_network", "execution_dag"},
}


def _temporal_plot_request_count(genes):
    """Count the saved curve and candidate panels before building a packet."""
    curves = sum(bool(row.get("time_grid")) for row in genes.values())
    comparisons = sum(any(
        candidate.get(criterion) is not None
        for candidate in row.get("candidate_scores", ())
        for criterion in ("aic", "bic", "gcv")
    ) for row in genes.values())
    # The saved adapter groups unavailable curves into one source panel when
    # the result contains no numerical curve.
    return max(1, curves) + comparisons


def _input_sample_cohort(service, bridge, matrix):
    """Resolve cohort IDs from the verified input artifacts, never outputs."""
    from .scientific_plot_contract import require_unique
    sample_ids = require_unique(matrix["sample_ids"], "bound_input_sample_ids")
    metadata_ref = bridge["original_refs"]["metadata"]
    metadata = _read_json(service.artifact_path(metadata_ref["artifact_id"]), 5_000_000, expected=metadata_ref)
    if not isinstance(metadata, dict) or not isinstance(metadata.get("samples"), list):
        raise ValueError("Bound input metadata lacks explicit sample cohort")
    metadata_ids = [row.get("sample_id") for row in metadata["samples"] if isinstance(row, dict)]
    require_unique(metadata_ids, "bound_metadata_sample_ids")
    if len(metadata_ids) != len(metadata["samples"]) or set(metadata_ids) != set(sample_ids):
        raise ValueError("Bound matrix and metadata sample cohorts disagree")
    transformation = _read_source(bridge["transformation_receipt_ref"], workspace=Path(service.root).parent)
    roles = {name: {bridge["original_refs"][name]["artifact_id"]} for name in ("matrix", "metadata")}
    for entry in transformation["transformations"]:
        roles[entry["source"]].add(entry["execution_artifact_id"])
    return {"sample_ids": list(sample_ids), "matrix_ref": bridge["original_refs"]["matrix"],
            "metadata_ref": metadata_ref, "role_artifact_ids": {key: sorted(value) for key, value in roles.items()},
            "execution_ports": {name: ref["artifact_id"] for name, ref in bridge["execution_refs"].items()}}


def _plot_sample_membership(actual, run, input_refs, cohort, linked_samples):
    """Check saved selections against source IDs and preserve their limits."""
    from .scientific_plot_sources import TOOL_FORMAT
    underlying = actual.get("result") if isinstance(actual, dict) and actual.get("format") == TOOL_FORMAT else actual
    lineage = isinstance(underlying, dict) and "lineage_graph" in underlying
    selected = list(underlying["pseudotime"]) if lineage else sorted(linked_samples)
    if not lineage and not selected:
        return None
    if cohort is None:
        raise ValueError("Saved sample/cell plots require the actual bound input sample cohort")
    actual_input_ids = {ref["artifact_id"] for ref in input_refs.values()}
    if any(not (actual_input_ids & set(cohort["role_artifact_ids"][role])) for role in ("matrix", "metadata")):
        raise ValueError("Saved plot sample cohort is not bound to the actual run matrix and metadata inputs")
    if any(input_refs.get(port, {}).get("artifact_id") != identity for port, identity in cohort["execution_ports"].items()):
        raise ValueError("Actual sample plot inputs differ from the bound matrix/metadata port association")
    allowed = set(cohort["sample_ids"])
    if set(selected) - allowed or set(linked_samples) - allowed:
        raise ValueError("Saved plot sample/cell IDs are outside the actual bound input cohort")
    membership = {"input_sample_ids": deepcopy(cohort["sample_ids"]), "sample_ids": selected,
        "input_matrix_ref": deepcopy(cohort["matrix_ref"]), "input_metadata_ref": deepcopy(cohort["metadata_ref"]),
        "input_sample_ids_sha256": digest(cohort["sample_ids"]), "selected_sample_ids_sha256": digest(selected),
        "selection_assessment": "exact_input_cohort" if set(selected) == allowed else "verified_input_subset_selection_rule_not_retained"}
    if lineage:
        provenance = underlying["provenance"]
        total, count = len(allowed), len(selected)
        for key, expected in (("input_sample_count", total), ("selected_sample_count", count)):
            if key in provenance and (type(provenance[key]) is not int or provenance[key] != expected):
                raise ValueError("Saved lineage " + key + " differs from actual bound input sample cohort")
        if "subsampled" in provenance:
            if type(provenance["subsampled"]) is not bool or provenance["subsampled"] != (count < total):
                raise ValueError("Saved lineage complete/subsampled declaration conflicts with actual cohort membership")
            membership["selection_assessment"] = "verified_declared_subset" if provenance["subsampled"] else "verified_complete_input_cohort"
        root = provenance.get("root_sample_id")
        if root is not None and (root not in selected or underlying["pseudotime"][root] != 0):
            raise ValueError("Saved lineage root sample is absent from its selected cohort or lacks zero pseudotime")
        if "root_sample_id" in run["parameters"] and root != run["parameters"]["root_sample_id"]:
            raise ValueError("Saved lineage root differs from the actual dispatch root sample")
        if "max_cells" in run["parameters"] and count != min(run["parameters"]["max_cells"], total):
            raise ValueError("Saved lineage selection count differs from actual dispatch selection limit")
        membership.update(root_id=underlying["lineage_graph"]["root"], root_sample_id=root,
            branch_assignments=deepcopy(underlying["branch_assignments"]), pseudotime=deepcopy(underlying["pseudotime"]))
    return membership


def _plot_projection(actual, run, method, ref, input_refs, universe, sample_cohort=None,
                     *, temporal_page=None):
    """Derive plot data from the actual source, retaining its exact cohort.

    This is output transport, not a new scientific comparison protocol. A
    plotted curve or graph does not acquire an estimand or qualification.
    """
    from .scientific_plot_sources import (SAVED_NETWORK_ALGORITHMS, TOOL_FORMAT, prepare_plot_input_from_saved,
                                         saved_tool_membership)
    from .scientific_plot_suite import prepare_scientific_plot_workspace
    implementation = method["contract"]["implementation"]
    tool = implementation.get("tool_name") if implementation["kind"] == "dispatcher" else None
    registered = {"fit_temporal_trajectory", "infer_lineage", "infer_regulatory_network",
                  "regulon_enrichment", "motif_enrichment"}
    if tool in registered:
        from devharness_server.method_adapter import dispatcher_contract
        if method["version_id"] != dispatcher_contract(tool).version_id:
            raise ValueError("Saved scientific projection requires the exact registered implementation")
        plot_parameters = run["parameters"]
        if temporal_page is not None:
            # The display page has a verified feature selection of its own.
            # This adapter input never replaces the governed dispatch parameters.
            plot_parameters = {**plot_parameters, "gene_list": temporal_page}
        document = {"format": TOOL_FORMAT, "tool_name": tool, "result": actual, "parameters": plot_parameters}
    else:
        document = actual
    packet = prepare_plot_input_from_saved(document)
    kinds = {key: [p["capability"] for p in packet["plots"] if p["capability"] in capabilities]
             for key, capabilities in _PLOT_OUTPUT_KINDS.items()}
    if not any(kinds.values()):
        return None, kinds
    workspace = prepare_scientific_plot_workspace(packet, source={"sha256": ref["sha256"],
        "run_id": run["run_id"], "kind": "retained"})
    linked_genes = {e["id"] for e in workspace["entities"] if e["kind"] == "gene"}
    if linked_genes - universe:
        raise ValueError("Saved plot features lack original input gene-universe lineage")
    linked_samples = {e["id"] for e in workspace["entities"] if e["kind"] in {"sample", "cell"}}
    sample_membership = _plot_sample_membership(actual, run, input_refs, sample_cohort, linked_samples)
    membership = saved_tool_membership(tool, actual, run["parameters"]) if tool else None
    if not tool and isinstance(actual, dict) and actual.get("format") == TOOL_FORMAT:
        membership = saved_tool_membership(actual["tool_name"], actual["result"], actual["parameters"])
    elif not tool and isinstance(actual, dict):
        evidence = actual.get("provenance", {}).get("evidence_type")
        if evidence in {"supplied curated TF-target overlap", "supplied sequence motif-hit calls"}:
            declared_tool = "regulon_enrichment" if evidence == "supplied curated TF-target overlap" else "motif_enrichment"
            membership = saved_tool_membership(declared_tool, actual, {})
    if membership and set(membership["background_ids"] or []) - universe:
        raise ValueError("Saved enrichment background differs from original input gene universe")
    if membership and any(set(ids) - universe for ids in (membership.get("term_member_ids") or {}).values()):
        raise ValueError("Saved enrichment term membership differs from original input gene universe")
    if membership and set(membership["selected_ids"] or []) - universe:
        raise ValueError("Saved enrichment query membership differs from original input gene universe")
    for request in packet["plots"]:
        if request["capability"] not in {"enrichment_dotplot", "motif_enrichment"}:
            continue
        data = request["data"]
        if data["universe"].get("unit") == "gene" and set(data["universe"].get("member_ids", [])) - universe:
            raise ValueError("Saved enrichment universe membership differs from original input genes")
        for term in data["terms"]:
            if term.get("member_unit") != "gene":
                continue
            for field in ("query_member_ids", "set_member_ids", "overlap_member_ids", "foreground_member_ids",
                          "background_member_ids", "foreground_hit_ids", "background_hit_ids"):
                if set(term.get(field, [])) - universe:
                    raise ValueError("Saved enrichment term/query member IDs lack original input gene lineage")
        if any(promoter["gene_id"] not in universe for promoter in data.get("promoters", [])):
            raise ValueError("Saved promoter gene lacks original input gene lineage")
    underlying = actual.get("result") if isinstance(actual, dict) and actual.get("format") == TOOL_FORMAT else actual
    candidate_universe = underlying.get("candidate_universe") if isinstance(underlying, dict) and (
        tool == "infer_regulatory_network" or underlying.get("provenance", {}).get("algorithm") in
        SAVED_NETWORK_ALGORITHMS) else None
    if candidate_universe and ({r[k] for r in candidate_universe for k in ("tf", "target")} - universe):
        raise ValueError("Saved network candidate universe lacks input gene lineage")
    return {"format": "devbench.rna-saved-plot-output/1", "run_id": run["run_id"],
        "method_version_id": run["method_version_id"], "artifact_ref": deepcopy(ref),
        "input_refs": deepcopy(input_refs), "cohort_sha256": digest(input_refs),
        "gene_universe_sha256": digest(sorted(universe)), "evidence_root": run["evidence"]["root_hash"],
        "packet": packet, "set_membership": membership,
        "sample_membership": sample_membership,
        "membership_scope": ("verified_temporal_report_page" if temporal_page is not None else
            "retained_dispatch_parameters" if tool else "retained_output_declaration_not_verified_dispatch_parameters"),
        "network_candidate_universe": candidate_universe,
        "scope": "saved_output_projection_not_comparative_scientific_qualification"}, kinds


def _extract(receipts, plan_reference, service, *, retained_table_sources=None, retained_plot_sources=None):
    plan, intent, workflow, bridge = _plan(plan_reference, service)
    runs = _runs(receipts, service, workflow, bridge)
    matrix_ref = bridge["original_refs"]["matrix"]
    matrix = _read_json(service.artifact_path(matrix_ref["artifact_id"]), 100_000_000, expected=matrix_ref)
    universe = set(matrix["gene_ids"])
    sample_cohort = _input_sample_cohort(service, bridge, matrix)
    required = {schema for profile in intent["profile_specs"] for schema in profile["required_output_schemas"]}
    tables, diagnostics, compact_runs, source_hashes, interventions, total = [], [], [], [], [], 0
    plot_outputs = {key: [] for key in _PLOT_OUTPUT_KINDS}
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
            typed_table = type(actual) is dict and actual.get("format") == "devbench.rna-gene-table/2"
            table, temporal_page = None, None
            if not typed_table and implementation["kind"] == "dispatcher" and implementation["tool_name"] == "fit_temporal_trajectory":
                count_ref = bridge["execution_refs"].get("counts")
                bound_axis = (matrix["gene_ids"] if count_ref is not None
                              and run["inputs"].get("counts") == count_ref["artifact_id"] else None)
                try:
                    table = validate_gene_table(_temporal_table(actual, run, method, input_gene_ids=bound_axis))
                except _TemporalFamilyUnavailable as error:
                    diagnostics.append({"run_id": run["run_id"], "status": "not_assessable",
                        "reason_codes": [str(error)], "source_refs": [source]})
                    continue
                if run["parameters"].get("fdr_scope") == "genome_wide":
                    temporal_page = [row["feature_id"] for row in table["rows"]]
            from .scientific_plot_sources import UnsupportedSavedPlotSource, MissingSavedPlotInputs
            plotted = False
            projection, kinds = None, {}
            if temporal_page is not None and _temporal_plot_request_count(actual["genes"]) > 64:
                diagnostics.append({"run_id": run["run_id"], "status": "not_assessable",
                    "reason_codes": ["temporal_output_page_exceeds_bounded_plot_projections"], "source_refs": [source]})
            else:
                try:
                    projection, kinds = _plot_projection(actual, run, method, ref, input_refs, universe,
                                                           sample_cohort, temporal_page=temporal_page)
                except (UnsupportedSavedPlotSource, MissingSavedPlotInputs) as error:
                    if isinstance(error, MissingSavedPlotInputs):
                        diagnostics.append({"run_id": run["run_id"], "status": "not_assessable",
                            "reason_codes": ["required_saved_plot_inputs_absent"], "source_refs": [source]})
            if projection is not None:
                if retained_plot_sources is None:
                    projection_source = _retained_source(service, projection)
                else:
                    projection_source = retained_plot_sources.get((run["run_id"], port))
                    if projection_source is None or _read_source(projection_source, MAX_TABLE_BYTES,
                            workspace=Path(service.root).parent) != projection:
                        raise ValueError("Derived scientific plot differs from actual retained output or cohort membership")
                total += projection_source["size_bytes"]
                if total > MAX_AGGREGATE_BYTES:
                    raise ValueError("Actual and derived scientific output aggregate exceeds its bound")
                for kind, capabilities in kinds.items():
                    if capabilities:
                        plot_outputs[kind].append({"run_id": run["run_id"], "port": port, "artifact_ref": ref,
                            "projection_ref": projection_source, "capabilities": capabilities,
                            "cohort_sha256": projection["cohort_sha256"], "evidence_root": run["evidence"]["root_hash"],
                            "frozen_comparison_eligible": frozen_eligible})
                plotted = True
            if typed_table:
                table = validate_gene_table(actual)
                table_source = source
            elif table is not None:
                if retained_table_sources is None:
                    table_source = _retained_source(service, table)
                else:
                    table_source = retained_table_sources.get((run["run_id"], port))
                    if table_source is None or _read_source(table_source, MAX_TABLE_BYTES, workspace=Path(service.root).parent) != table:
                        raise ValueError("Derived temporal table differs from the actual retained result")
                total += table_source["size_bytes"]
                if total > MAX_AGGREGATE_BYTES:
                    raise ValueError("Actual and derived result aggregate exceeds its bound")
                reasons = ["temporal_endpoint_and_omnibus_hypothesis_differ", "unknown_effect_comparability"]
                if temporal_page is None and any(row["status"] == "filtered" for row in table["rows"]):
                    reasons.append("temporal_requested_bh_denominator_includes_untested_filtered_genes")
                diagnostics.append({"run_id": run["run_id"], "status": "not_assessable",
                    "reason_codes": reasons, "source_refs": [source, table_source]})
            else:
                if not plotted:
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
        "runs": compact_runs, "gene_tables": tables, **plot_outputs, "diagnostics": diagnostics,
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
    retained_plot_sources = {}
    for kind in _PLOT_OUTPUT_KINDS:
        if not isinstance(record[kind], list):
            raise ValueError("Saved scientific output descriptors must be lists")
        for item in record[kind]:
            if not isinstance(item, dict) or not {"run_id", "port", "projection_ref"} <= set(item):
                raise ValueError("Unsupported/unretained sets, curves or graphs cannot acquire numeric authority")
            key = (item["run_id"], item["port"])
            if key in retained_plot_sources and retained_plot_sources[key] != item["projection_ref"]:
                raise ValueError("Saved scientific projection references disagree for the same actual output")
            retained_plot_sources[key] = item["projection_ref"]
    expected = _extract([run["run_id"] for run in record["runs"]], record["assessment_plan_ref"], artifact_service,
        retained_table_sources={(table["run_id"], table["port"]): table["table_ref"] for table in record["gene_tables"]},
        retained_plot_sources=retained_plot_sources)
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
