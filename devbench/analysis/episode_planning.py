"""Question-to-proposal coordination using existing input, selector and review APIs.

Proposals remain unreviewed suggestions. No estimator, scientific acceptance gate,
free-form code execution, provider session or approval is introduced on import.
"""
from dataclasses import asdict
from pathlib import Path
import json
import time

from .checkpoints import atomic_json, digest
from .episode_agent import AgentLimits, InvalidSelection, selector_configuration
from .episode_proposals import _absolute, _input_identity, prepare_proposal, require_disjoint_output
from .episodes import EpisodeLimits, _json_copy, _public_only, _sources, _support, normalize_episode_configuration

_WORKFLOWS = {"bulk_mean_contrast": ("bulk", "mean_contrast"),
              "cellular_state_contrast": ("scrna", "cell_state_effect")}


def proposal_schema(context):
    """The actor may describe a contrast, ask for information, or refuse."""
    workflows = context["allowed_workflows"]
    if context.get("workflow_family") == "backed_workbench":
        if not isinstance(workflows, list) or any(name != "scrna_qc_integration_annotation/1" for name in workflows):
            raise ValueError("Unregistered backed-workbench workflow")
        variants = [{"type": "object", "additionalProperties": False, "required": ["kind", field],
            "properties": {"kind": {"const": kind}, field: {"type": "string", "minLength": 1, "maxLength": 4000}}}
            for kind, field in (("request_information", "message"), ("refuse", "reason"))]
        if workflows:
            variants.append({"type": "object", "additionalProperties": False, "required": ["kind", "workflow", "rationale"],
                "properties": {"kind": {"const": "propose"}, "workflow": {"enum": workflows},
                               "rationale": {"type": "string", "minLength": 1, "maxLength": 4000}}})
        return {"type": "object", "oneOf": variants}
    levels = context["observed"].get("condition_levels", [])
    if not isinstance(workflows, list) or any(name not in _WORKFLOWS for name in workflows):
        raise ValueError("Unregistered planning workflow")
    if (not isinstance(levels, list) or len(levels) > 50 or any(
            not isinstance(value, str) or not value.strip() or len(value) > 300 for value in levels)):
        raise ValueError("Condition labels exceed the bounded proposal vocabulary")
    variants = [{"type": "object", "additionalProperties": False,
        "required": ["kind", field], "properties": {"kind": {"const": kind},
        field: {"type": "string", "minLength": 1, "maxLength": 4000}}}
        for kind, field in (("request_information", "message"), ("refuse", "reason"))]
    if workflows and len(levels) >= 2:
        variants.append({"type": "object", "additionalProperties": False,
            "required": ["kind", "workflow", "reference_condition", "comparison_condition", "rationale"],
            "properties": {"kind": {"const": "propose"}, "workflow": {"enum": workflows},
                "reference_condition": {"enum": levels}, "comparison_condition": {"enum": levels},
                "rationale": {"type": "string", "minLength": 1, "maxLength": 4000}}})
    return {"type": "object", "oneOf": variants}


def _target(assay, measurement, reference=None, comparison=None):
    cellular = assay == "scrna"
    return {"target_id": "cell_state_effect" if cellular else "mean_contrast",
        "estimand": "Within-type donor mean expression contrast" if cellular else "Biological-unit mean expression contrast",
        "units": "unchanged supplied expression scale" if measurement == "continuous" else "log2 relative expression",
        "contrast": f"{comparison} minus {reference}" if reference is not None else "Input inspection; contrast not yet proposed",
        "reference_population": "Declared biological units with observed support",
        "prediction_reference": "Observed eligible donor population" if cellular else "Observed eligible biological-unit population"}


def _workbench_dataset(question, dataset, tier):
    from .workbench_proposals import _configuration
    if (set(dataset) - {"transport", "source", "model_path", "parameters", "declarations", "run_timeout_seconds"}
            or not {"source", "parameters", "declarations"} <= dataset.keys()):
        raise ValueError("Bind the existing backed-workbench settings, declarations and external source")
    data = _json_copy(dataset)
    config, eligible = _configuration(question, data["parameters"], data["declarations"], data.get("run_timeout_seconds", 3600))
    if config["parameters"]["execution_tier"] != tier:
        raise ValueError("Workbench execution tier disagrees with the request")
    source = _absolute(data["source"])
    model = _absolute(data["model_path"]) if data.get("model_path") is not None else None
    return config, eligible, str(source), str(model) if model else None


def _workbench_context(question, config, eligible, inspected):
    params, preview = config["parameters"], inspected["preview"]
    available = eligible and preview.get("observed_batches") is not None and preview["observed_batches"] > 0
    settings = {key: params[key] for key in ("counts_layer", "batch_key", "symbol_column", "min_genes", "mad_limit",
        "max_mito_percent", "n_top_genes", "n_pcs", "resolution", "correction", "scvi_epochs")}
    settings.update(marker_labels=sorted(params["markers"]), model_supplied=params["model_path"] is not None)
    return {"decision_kind": "analysis_proposal", "workflow_family": "backed_workbench", "question": question,
        "declarations": config["declarations"], "fixed_settings": settings,
        "observed": {key: preview[key] for key in ("cells", "genes", "observed_batches", "integration_applicability", "annotation")},
        "allowed_workflows": [params["workflow"]] if available else [],
        "admission": _admission_summary(available, "scrna_qc_integration_annotation", "backed_metadata_preview",
            ["declared_umi_counts_and_capture_units_required", "computational_labels_are_concordance_references"]),
        "resource_limits": {"worker_memory_MiB": params["memory_mb"], "stage_seconds": params["stage_timeout_seconds"],
            "scvi_stage_seconds": params["scvi_timeout_seconds"], "run_seconds": config["run_timeout_seconds"],
            "planned_execution_tier": params["execution_tier"]},
        "limits": ["This is the existing QC, integration, clustering and annotation workflow, not a condition contrast.",
            "Propose it only if it answers the question; otherwise ask for information or refuse.",
            "No expression model, clustering or annotation has run during this input inspection.",
            "Provenance and capture mapping are caller declarations, not independently verified experimental facts.",
            "Existing computational labels support concordance, not accuracy or reproduction of a missing classifier.",
            "Settings, input bindings and resource limits are fixed; the agent cannot approve or execute its suggestion.",
            "Clinical, causal, differential-expression, lineage and general snRNA claims are outside this workflow."]}


def _dataset(question, dataset, limits, fixed_options, tier):
    if (not isinstance(dataset, dict) or set(dataset) - {"assay", "input_kind", "public_task", "inputs"}
            or not {"assay", "public_task", "inputs"} <= set(dataset)):
        raise ValueError("Bind an assay, dataset declarations and the existing three input roles")
    values = _json_copy(dataset)
    task = values["public_task"]
    _public_only(task)
    if not isinstance(task, dict) or {"questions", "targets"} & task.keys():
        raise ValueError("Dataset declarations must not contain pre-authored question targets")
    from devbench.study.biology_tool_io import validate_public_task
    inspection = dict(task, questions=[_target(values["assay"], "continuous")])
    projected = validate_public_task(inspection)
    task.update(measurement=projected["measurement"], unit_hierarchy=projected["unit_hierarchy"],
                population_unit_label=projected["unit_hierarchy"]["population_unit_label"],
                context=projected["analysis_context"])
    task.setdefault("analysis_columns", {"condition": "condition", "time": "numeric_time", "dose": None,
        "covariate": None, "population_unit": task["population_unit_label"], "batches": []})
    kind = values.get("input_kind", "normalized_expression" if task["measurement"] == "continuous" else "count_matrix")
    # Pure validation only: actual feature identities are read by the bounded
    # worker and retained before any proposal is compiled or sent for review.
    candidate = dict(task, questions=[_target(values["assay"], task["measurement"])])
    candidate.setdefault("gene_ids", ["input_inspection_placeholder"])
    normalize_episode_configuration(question=question, assay=values["assay"], public_task=candidate,
        limits=limits, fixed_options=fixed_options, input_kind=kind, execution_tier=tier)
    return values["assay"], kind, task, values["inputs"]


def _admission_summary(available, scope, evidence, restrictions):
    """Separate intake availability from scientific judgment and actor actions."""
    return {"format": "devbench.proposal-admission/1", "requested_claim": "not_classified",
        "scientific_status": {"identifiability": "not_established", "population_support": "not_evaluated",
                              "statistical_calibration": "not_established"},
        "evidence_status": {"experimental_provenance": "not_independently_verified", "intake_check": evidence},
        "software_support": {"proposal_candidate_available": bool(available),
            "design_compatibility": "not_yet_validated", "restrictions": restrictions},
        "permitted_interpretation": {"scope": scope, "requires_successful_registered_validation": True,
            "excluded_claims": ["causal_identification", "predictive_accuracy", "clinical_validation"]},
        "available_agent_actions": (["propose"] if available else []) + ["request_information", "refuse"]}


def _design_summary(data, condition, batch_columns, audit):
    """Expose aggregate observed mappings, without sample IDs or model fitting."""
    from collections import Counter

    metadata = data.metadata
    unit = data.unit_hierarchy["population_unit_label"]
    batches = list(dict.fromkeys(batch_columns))
    coordinates = list(dict.fromkeys([condition, *batches]))
    absent = [name for name in coordinates if name not in metadata]
    missing = {name: int((metadata[name].isna() | metadata[name].astype(str).str.strip().eq("")).sum())
               for name in coordinates if name in metadata}
    verified_units = audit["identity_and_nesting_valid"] is True
    condition_known = condition in metadata and missing.get(condition, 0) == 0
    patterns, varying, layout = None, None, "unknown"
    if verified_units and condition_known:
        memberships = Counter(tuple(sorted(set(frame[condition].astype(str))))
                              for _, frame in metadata.groupby(unit, sort=False))
        patterns = [{"conditions": list(levels), "biological_units": count}
                    for levels, count in sorted(memberships.items())]
        levels = tuple(sorted(set(metadata[condition].astype(str))))
        layout = ("independent_conditions" if all(len(values) == 1 for values in memberships)
                  else "complete_repeated" if all(values == levels for values in memberships)
                  else "incomplete_repeated")
        present = [name for name in coordinates if name in metadata]
        varying = int((metadata.groupby(unit)[present].nunique(dropna=False) > 1).any(axis=1).sum())
    support = []
    if condition_known:
        for column in batches:
            if column not in metadata:
                continue
            rows = []
            for ordinal, (_, batch) in enumerate(metadata.groupby(column, dropna=False, sort=True)):
                groups = [{"condition": str(label), "observations": len(frame),
                           "biological_units": int(frame[unit].nunique()) if verified_units else None}
                          for label, frame in batch.groupby(condition, sort=True)]
                # Codes are local to this summary; original batch labels can
                # identify specimens and are not needed to show design overlap.
                rows.append({"level_code": f"batch_{ordinal}", "conditions": groups})
            support.append({"column": column, "levels": rows})
    return {"scope": "observed_metadata_mappings_not_independent_experimental_provenance",
        "identifiability": "not_evaluated", "missing_columns": absent, "missing_values": missing,
        "unit_condition_patterns": patterns, "repeated_unit_layout": layout,
        "units_with_varying_design_coordinates": varying, "batch_condition_support": support,
        "capture_units": {"column_present": "capture_batch" in metadata,
            "observed_labels": int(metadata["capture_batch"].nunique()) if "capture_batch" in metadata else None,
            "provenance": "metadata_labels_not_independently_verified_capture_history"}}


def _inspect_worker(task, paths, assay):
    """Bounded input inspection only, with no expression model or annotation fit."""
    from .resources import configure_threads
    configure_threads()
    from devbench.study.biology_tool_io import load_observed_data
    from devbench.study.biology_tools_bulk import _unit_audit
    data = load_observed_data(dict(task, questions=[_target(assay, task["measurement"])]),
        counts_path=paths["expression"], metadata_path=paths["metadata"],
        annotations_path=paths["annotations"], allow_data_execution=True)
    audit = _unit_audit(data)
    if assay == "scrna":
        from devbench.study.biology_tools_singlecell import _units, _Unavailable
        try:
            _units(data)
        except _Unavailable as error:
            audit.update(identity_and_nesting_valid=False, biological_unit_count=None,
                         problems=list(dict.fromkeys([*audit["problems"], str(error)])))
    condition = task["analysis_columns"]["condition"]
    present = condition in data.metadata
    levels = sorted(set(data.metadata[condition].astype(str))) if present else []
    unit = data.unit_hierarchy["population_unit_label"]
    counts = {level: int(data.metadata.loc[data.metadata[condition].astype(str) == level, unit].nunique())
              for level in levels} if present and audit["identity_and_nesting_valid"] else None
    return {"gene_ids": list(data.gene_ids), "observed": {
        "gene_count": len(data.gene_ids), "observation_count": len(data.sample_ids),
        "matrix_bytes": int(data.expression.nbytes), "measurement": data.measurement,
        "condition_column_present": present, "condition_levels": levels,
        "biological_units": audit, "declared_units_per_condition": counts,
        "design_summary": _design_summary(data, condition, task["analysis_columns"]["batches"], audit),
        "feature_alignment_checked": True,
        "cell_annotation_reference_available": bool(data.annotations.get("cell_annotation_reference")),
        "scope": "input_consistency_not_independent_experimental_provenance_or_model_fit"}}


def _context(question, assay, kind, task, inspection, fixed_options):
    observed = inspection["observed"]
    candidate = dict(task, questions=[_target(assay, task["measurement"])])
    route = "cellular_state_contrast" if assay == "scrna" else "bulk_mean_contrast"
    method = "cell_type_state_analysis" if assay == "scrna" else "mean_condition_analysis"
    floor = max(3, fixed_options.get(method, {}).get("minimum_units", 3))
    unit_counts = observed.get("declared_units_per_condition") or {}
    supported = _support(assay, kind, candidate)
    usable = (supported
              and observed.get("biological_units", {}).get("identity_and_nesting_valid") is True
              and len(unit_counts) >= 2 and all(count >= floor for count in unit_counts.values())
              and (assay != "bulk" or len(unit_counts) == 2)
              and (assay != "scrna" or observed.get("cell_annotation_reference_available")))
    return {"decision_kind": "analysis_proposal", "question": question,
        "declarations": {"assay": assay, "input_kind": kind, "measurement": task["measurement"],
            "biological_unit_hierarchy": {key: task["unit_hierarchy"][key] for key in (
                "population_unit", "population_unit_label", "specimen_label", "library_label", "identity_status")
                if key in task["unit_hierarchy"]},
            "column_roles": {key: task["analysis_columns"][key] for key in ("condition", "population_unit", "batches")},
            "evidence_scope": "supplied_declarations_not_independently_authenticated"},
        "observed": observed, "allowed_workflows": [route] if usable else [],
        "admission": _admission_summary(usable,
            "unavailable_for_declared_input" if not supported else
                "declared_scale_expression_association" if task["measurement"] == "continuous" else "relative_expression_association",
            "biological_unit_metadata_consistent" if observed.get("biological_units", {}).get("identity_and_nesting_valid")
                else "biological_unit_metadata_unverified_or_inconsistent",
            ["complete_paired_or_independent_layouts_only", "population_specific_support_required", "marker_annotation_is_uncertain"]
                if assay == "scrna" else ["bulk_repeated_coordinates_not_supported"]),
        "fixed_options": fixed_options,
        "limits": ["Propose only if the supported estimand answers the question; otherwise clarify or refuse.",
            "Bulk mean contrast and donor-aware within-type mean contrast are the current episode routes.",
            "No causal, absolute-abundance, trajectory, pathway or general distributional conclusion follows from these routes.",
            "The complete declared feature family remains fixed; no data-dependent filtering is proposed here.",
            "Design identifiability, repeated-donor layouts, per-type support and model diagnostics remain to be checked.",
            "A proposal is unreviewed. Operator review and separate execution permission remain required.",
            "Refusal correctness and scientific appropriateness have not been independently evaluated."]}


def _compile(question, assay, kind, task, paths, decision, limits, options, tier):
    reference, comparison = decision["reference_condition"], decision["comparison_condition"]
    if reference == comparison:
        raise ValueError("A condition cannot be compared with itself")
    if _WORKFLOWS[decision["workflow"]][0] != assay:
        raise ValueError("Proposed workflow changes the declared assay")
    task = _json_copy(task)
    task["questions"] = [_target(assay, task["measurement"], reference, comparison)]
    task["context"]["condition_labels"] = [reference, comparison]
    task["context"]["requested_condition_contrast"] = {"numerator": comparison, "denominator": reference}
    configuration = normalize_episode_configuration(question=question, assay=assay, public_task=task,
        limits=limits, fixed_options=options, input_kind=kind, execution_tier=tier)
    return {**configuration, "inputs": paths}


def propose_analysis(output, *, question, dataset, selector, episode_limits=None, agent_limits=None,
                     fixed_options=None, execution_tier="local_supervised", allow_data_read=False,
                     allow_model_execution=False, model_data_scope="local_only"):
    """One bounded suggestion followed by a separately reviewed proposal.

    ``selector`` is trusted coordinator code. Live Codex uses its existing OS/
    provider preflights. The scope declaration is caller-asserted, not a PHI
    detector. Private clinical inputs are not an authorized external-model scope.
    Original paths are content-bound and rechecked; the existing reviewed episode
    creates the execution snapshot later. This function never runs an analysis.
    """
    if allow_data_read is not True:
        raise PermissionError("Question-to-proposal preparation requires explicit data-reading authorization")
    selector_config = selector_configuration(selector)
    if selector_config.get("backend") == "codex-cli" and allow_model_execution is not True:
        raise PermissionError("Codex proposal selection requires explicit model permission")
    if allow_model_execution is True and model_data_scope not in {"synthetic", "public"}:
        raise PermissionError("External model use requires declared synthetic or public context scope")
    workbench = isinstance(dataset, dict) and dataset.get("transport") == "backed_workbench"
    policy = agent_limits or AgentLimits(max_selections=1)
    if not isinstance(policy, AgentLimits):
        raise TypeError("Use existing agent resource limits")
    if workbench:
        if episode_limits is not None or fixed_options is not None:
            raise ValueError("The workbench keeps its own fixed settings and resource profile, not table episode limits")
        config, eligible, source, model = _workbench_dataset(question, dataset, execution_tier)
        paths = {"source": source, **({"model": model} if model else {})}
        stage_seconds = min(config["parameters"]["stage_timeout_seconds"], config["run_timeout_seconds"])
        inspection_memory = config["parameters"]["memory_mb"]
        total_seconds = policy.timeout_seconds
    else:
        limits = episode_limits or EpisodeLimits()
        if not isinstance(limits, EpisodeLimits):
            raise TypeError("Use existing episode resource limits")
        options = _json_copy(fixed_options or {})
        assay, kind, task, paths = _dataset(question, dataset, limits, options, execution_tier)
        stage_seconds, inspection_memory = limits.stage_timeout_seconds, limits.memory_mb
        total_seconds = min(limits.timeout_seconds, policy.timeout_seconds)
    root = require_disjoint_output(output, paths.values())
    identities = _input_identity(paths, limits.max_input_bytes) if not workbench else None
    from devbench.study.permissions import require_protected_storage
    from devharness_core.provenance import ExecutionDAG
    require_protected_storage(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    audit = ExecutionDAG(root / "audit", actor_id="analysis-proposal-coordinator")
    started = time.monotonic()
    deadline = started + total_seconds
    if workbench:
        from .workbench_proposals import _source_identity
        source_id = _source_identity()
        sources = {"workbench_source_bundle": source_id}
        request = {"format": "devbench.analysis-request/1", "question": question, "workflow_family": "backed_workbench",
            "workbench_configuration": config, "source_path": source, "model_path": model,
            "agent_limits": asdict(policy), "effective_max_selections": 1, "execution_tier": execution_tier,
            "model_data_scope": model_data_scope, "selector": selector_config, "source_sha256": source_id}
    else:
        sources = _sources()
        request = {"format": "devbench.analysis-request/1", "question": question, "assay": assay,
        "input_kind": kind, "public_task": task, "input_paths": paths, "input_identity": identities,
        "fixed_options": options, "episode_limits": asdict(limits), "agent_limits": asdict(policy),
        "effective_max_selections": 1, "execution_tier": execution_tier,
        "model_data_scope": model_data_scope, "selector": selector_config, "source_sha256": digest(sources)}
    result = {"format": "devbench.analysis-proposal-run/1", "status": "infrastructure_failure",
        "proposal_receipt": None, "decision": None, "analysis_executed": False,
        "scientific_appropriateness": "not_established", "refusal_correctness": None,
        "model_sessions_attempted": 0, "tokens": None, "cost_usd": None, "actual_model": None}
    spec = None

    def remaining():
        seconds = deadline-time.monotonic()
        if seconds <= 0:
            raise TimeoutError("proposal_deadline_exhausted")
        return seconds

    def unchanged():
        remaining()
        if workbench:
            from .runtime import verify_data_identity
            from .checkpoints import sha256
            verify_data_identity(Path(source), config["parameters"]["data_sha256"])
            if model is not None and sha256(model) != config["parameters"]["model_sha256"]:
                raise ValueError("planning_model_identity_changed")
            if _source_identity() != source_id:
                raise ValueError("planning_source_identity_changed")
        else:
            if _input_identity(paths, limits.max_input_bytes) != identities:
                raise ValueError("planning_input_identity_changed")
            if _sources() != sources:
                raise ValueError("planning_source_identity_changed")
        remaining()

    try:
        atomic_json(root / "request.json", request)
        atomic_json(root / "source-identity.json", sources)
        from .runtime import observe_runtime
        atomic_json(root / "runtime.json", observe_runtime(execution_tier, detect_gpu=False))
        if not workbench and kind == "raw_reads":
            inspection = {"gene_ids": task.get("gene_ids", []), "observed": {
                "condition_levels": [], "scope": "not_inspected_unsupported_raw_read_input"}}
        else:
            from devharness_core.watchdog import run_sandboxed, worker_limits
            supervision = {}
            try:
                timeout = min(remaining(), stage_seconds)
                growth_window = config["parameters"].get("growth_window_seconds", 0) if workbench else 0
                with worker_limits(deadline=time.monotonic()+timeout, max_mem_mb=inspection_memory, scratch_dir=root,
                                   growth_window_seconds=growth_window):
                    if workbench:
                        from .workbench_proposals import _inspection_worker
                        worker, arguments = _inspection_worker, (source, config["parameters"], model)
                    else:
                        worker, arguments = _inspect_worker, (task, paths, assay)
                    inspection = run_sandboxed(worker, arguments, max_mem_mb=inspection_memory, timeout_sec=timeout,
                                               diagnostics=supervision, growth_window_seconds=growth_window)
            finally:
                atomic_json(root / "inspection-supervision.json", supervision)
        atomic_json(root / "inspection.json", inspection)
        unchanged()
        if workbench:
            context = _workbench_context(question, config, eligible, inspection)
        else:
            task = _json_copy(task)
            task["gene_ids"] = inspection["gene_ids"]
            context = _context(question, assay, kind, task, inspection, options)
        _public_only(context)
        encoded = json.dumps(context, allow_nan=False, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode()) > policy.max_context_bytes:
            raise ValueError("proposal_context_budget_exhausted_no_dispatch")
        schema = proposal_schema(context)
        atomic_json(root / "context.json", context)
        atomic_json(root / "answer_schema.json", schema)
        selected = root / "selector"
        selected.mkdir(mode=0o700)
        atomic_json(root / "selection-reservation.json", {"sequence": 1,
            "remaining_seconds": remaining(), "model_execution_authorized": allow_model_execution is True})
        selection = selector.select(_json_copy(context), selected,
            timeout_seconds=min(remaining(), policy.session_timeout_seconds), memory_mb=min(inspection_memory, policy.memory_mb),
            allow_model_execution=allow_model_execution)
        evidence, decision = _json_copy(selection.evidence), _json_copy(selection.action)
        atomic_json(root / "selection.json", {"decision": decision, "evidence": evidence})
        for field in ("model_sessions_attempted", "tokens", "cost_usd", "actual_model"):
            result[field] = evidence.get(field)
        unchanged()
        import jsonschema
        try:
            _public_only(decision)
            jsonschema.Draft202012Validator(schema).validate(decision)
            result["decision"] = decision
            if decision["kind"] == "propose":
                if workbench:
                    from .workbench_proposals import _proposal_record
                    spec = _proposal_record(config, eligible, inspection, source_id, supervision)
                else:
                    spec = _compile(question, assay, kind, task, paths, decision, limits, options, execution_tier)
                atomic_json(root / "compiled-spec.json", spec)
            else:
                spec = None
        except (ValueError, TypeError, jsonschema.ValidationError) as error:
            raise InvalidSelection("Invalid proposed analysis: " + type(error).__name__) from error
        if spec is not None:
            result["status"] = "proposal_selected"
        else:
            result["status"] = "awaiting_information" if decision["kind"] == "request_information" else "refusal"
    except InvalidSelection as error:
        result.update(status="invalid_proposal", reason=str(error)[:2000], error_type=type(error).__name__)
    except (Exception, KeyboardInterrupt) as error:
        result.update(status="infrastructure_failure", reason=str(error)[:2000], error_type=type(error).__name__)
    finally:
        # A killed/failed selector may have evidence even without returning a
        # Selection. Preserve and bind its original files, including failures.
        evidence_path = root / "selector/evidence.json"
        if evidence_path.is_file():
            try:
                from .episode_codex import _read_evidence
                evidence = _read_evidence(evidence_path)
                for field in ("model_sessions_attempted", "tokens", "cost_usd", "actual_model"):
                    result[field] = evidence.get(field)
            except (OSError, ValueError, TypeError, AttributeError):
                result.update(status="infrastructure_failure", reason="selector_evidence_unreadable",
                              model_sessions_attempted=None, tokens=None, cost_usd=None, actual_model=None)
        result["wall_seconds"] = time.monotonic()-started
        result["evidence_capture_complete"] = True
        for path in sorted(root.iterdir()):
            if path.name in {"audit", ".work"}:
                continue
            try:
                audit.add_artifact(path, max_bytes=20_000_000, max_entries=5000)
            except (OSError, ValueError, RuntimeError) as error:
                result.update(status="infrastructure_failure", reason="planning_evidence_capture_failed:"+type(error).__name__,
                              proposal_receipt=None, evidence_capture_complete=False)
        atomic_json(root / "result.json", result)
        audit.add_artifact(root / "result.json")
        audit.append("analysis.proposal_attempt", {"status": result["status"], "request_sha256": digest(request),
            "analysis_executed": False, "scientific_appropriateness": "not_established"})
        authoring_receipt = audit.finalize(analysis_fingerprint=digest(request))
        atomic_json(root / "authoring-root-receipt.json", authoring_receipt)
    if result["status"] == "proposal_selected":
        # The authoring DAG is closed and verified before any reviewable
        # proposal is created. Publication has its own receipt, avoiding a
        # circular claim that a proposal validates the run that created it.
        publication = ExecutionDAG(root / "publication-audit", actor_id="proposal-publication-coordinator")
        result["authoring_receipt"] = authoring_receipt
        try:
            unchanged()
            authoring = {"scope": "unreviewed_agent_suggestion", "manifest_path": authoring_receipt["manifest_path"],
                "root_hash": authoring_receipt["root_hash"], "request_sha256": digest(request),
                "context_sha256": digest(context), "decision_sha256": digest(decision),
                "selector_evidence_sha256": digest(selection.evidence)}
            if workbench:
                from .workbench_proposals import _publish_authored_workbench
                result["proposal_receipt"] = _publish_authored_workbench(root / "proposal", spec, authoring)
            else:
                result["proposal_receipt"] = prepare_proposal(root / "proposal", spec,
                    allow_data_read=True, authoring=authoring)
            result["status"] = "proposal_ready_for_review"
        except (Exception, KeyboardInterrupt) as error:
            result.update(status="infrastructure_failure", proposal_receipt=None,
                          reason=str(error)[:2000], error_type=type(error).__name__)
        result["wall_seconds"] = time.monotonic()-started
        atomic_json(root / "publication.json", result)
        publication.add_artifact(root / "publication.json")
        publication.append("analysis.proposal_publication", {"status": result["status"],
            "authoring_root": authoring_receipt["root_hash"],
            "proposal_root": (result["proposal_receipt"] or {}).get("root_hash")})
        result["receipt"] = publication.finalize(analysis_fingerprint=digest(request))
    else:
        result["receipt"] = authoring_receipt
    atomic_json(root / "root-receipt.json", result["receipt"])
    return result


class RecordedPlanningSelector:
    """Explicit offline decision input at the same boundary as the live selector."""

    def __init__(self, answer):
        self.answer = _json_copy(answer)

    def configuration(self):
        return {"backend": "recorded_planning_answer", "answer_sha256": digest(self.answer)}

    def select(self, context, output, **kwargs):
        from .episode_agent import Selection
        return Selection(_json_copy(self.answer), {"backend": "recorded_planning_answer", "model_sessions_attempted": 0,
            "tokens": None, "cost_usd": None, "actual_model": None, "model_identity_verified": False})


def planning_cli(args):
    """Existing episode CLI's explicit proposal mode; never starts an episode."""
    from .episode_cli import _read
    from .episode_agent import Selection
    request = _read(args.plan_request)
    if (not isinstance(request, dict) or set(request) - {"question", "dataset", "limits", "agent_limits", "fixed_options", "execution_tier"}
            or not {"question", "dataset"} <= request.keys()):
        raise ValueError("Use a question and dataset request, not an authored analysis specification")
    data = _json_copy(request["dataset"])
    workbench = data.get("transport") == "backed_workbench"
    if workbench:
        for key in ("source", "model_path"):
            if data.get(key) is not None:
                path = Path(data[key])
                data[key] = str(path if path.is_absolute() else (args.plan_request.parent / path).resolve())
    else:
        data["inputs"] = {name: str(Path(value) if Path(value).is_absolute() else
            (args.plan_request.parent / value).resolve()) for name, value in data["inputs"].items()}
    policy = AgentLimits(**request.get("agent_limits", {"max_selections": 1}))
    if args.codex_model:
        from .episode_codex import CodexActionSelector
        selector = CodexActionSelector(requested_model=args.codex_model, workspace_parent=args.agent_workspaces,
            private_root=args.output, memory_mb=policy.memory_mb)
    else:
        selector = RecordedPlanningSelector(_read(args.plan_answer))
    return propose_analysis(args.output, question=request["question"], dataset=data, selector=selector,
        episode_limits=(None if workbench and "limits" not in request else EpisodeLimits(**request.get("limits", {}))), agent_limits=policy,
        fixed_options=request.get("fixed_options"), execution_tier=request.get("execution_tier",
            data["parameters"]["execution_tier"] if workbench else "local_supervised"),
        allow_data_read=True, allow_model_execution=args.allow_model_execution,
        model_data_scope=args.model_data_scope or "local_only")
