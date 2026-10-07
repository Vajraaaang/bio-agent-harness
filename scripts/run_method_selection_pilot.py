"""Prepare, execute once, then grade a protected four-case synthetic pilot.

Live actions use ScientificMethodPlanner, CodexActionSelector and MethodService's
normal OCI executor. No candidate code runs in this coordinator. Each case has
one attempt; failures and returned-but-invalid responses remain evidence.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import importlib.metadata
import json
from pathlib import Path
import secrets
import sys
import time

SOURCE = Path(__file__).resolve().parents[1]
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from devbench.analysis.method_selection_evaluation import (
    BOUNDS, DIAGNOSTIC_RUBRIC, PROFILE_RUBRIC, digest, evaluate_pilot, file_digest, plot_pilot, prepare_pilot,
    public_case, require_live_open, seal_decision, verify_preparation, write_once, plot_executed_outputs,
)
from devbench.analysis.method_validation import finite_json
from devbench.analysis.method_executor import _path


IMAGE = "onlyoffice/documentserver@sha256:290a0a1406a485acc8a6bc20418ac7158c5eec78b4844c218f8f975a9800ddbc"
ACTOR = "bounded-synthetic-pilot-coordinator"


def frozen_parameters(candidate):
    result = {}
    for row in candidate["parameter_candidates"]:
        if row["name"] in result or row["origin"] == "user":
            raise ValueError("Pilot parameter names must be unique and cannot invent user requirements")
        result[row["name"]] = row["value"]
    return result


def _value_schema(value):
    kind = {str: "string", bool: "boolean", int: "integer", float: "number", list: "array", dict: "object", type(None): "null"}.get(type(value))
    if kind is None:
        raise ValueError("Parameter must be finite JSON")
    return {"type": kind}


def metric_catalog(measurements, *, diagnostics=False, profiles=False):
    # Shared immutable input dimensions are descriptive, never method quality.
    result = ["/shape/before/0", "/shape/before/1"]
    for field in measurements:
        escaped = field.replace("~", "~0").replace("/", "~1")
        if not diagnostics and not profiles:
            result.extend(["/reference_qc_means/" + escaped + "/after",
                       "/reference_qc_means/" + escaped + "/absolute_standardized_shift"])
            result.extend("/reference_qc_quantiles/" + escaped + "/after/" + str(index) for index in range(5))
        if diagnostics and not profiles:
            result.extend("/diagnostic_measurements/by_measurement/" + escaped + "/" + name for name in
                          ("input_mean", "flagged_mean", "absolute_standardized_flagged_shift"))
        if profiles:
            result.extend("/profile_metrics/by_measurement/" + escaped + "/" + name for name in
                          ("available_fraction", "mean_score", "max_score"))
    if diagnostics or profiles:
        result.extend(["/diagnostic_measurements/flag_rate", "/diagnostic_measurements/available_fraction"])
    if profiles:
        result.extend(["/diagnostic_measurements/decision_fraction", "/profile_metrics/aggregate_mean", "/profile_metrics/aggregate_max"])
    return result


def interface_for(candidate, measurements, has_samples, lock_sha, *, measurement_semantics=None, diagnostic_profiles=False):
    parameters = frozen_parameters(candidate)
    example = measurements[0].replace("~", "~0").replace("/", "~1")
    policy = {"comparator": "observation_selection", "invariants": [], "counts_layer": "not_applicable",
              "qc_metrics": list(measurements)}
    if has_samples:
        policy["sample_key"] = "sample"
    contract = {"format": "devharness.method/1", "method_id": "pilot.generated." + digest(candidate)[:16],
            "name": candidate["name"], "lifecycle": "custom", "implementation_trust": "unreviewed",
            "inputs": {"dataset": {"artifact_type": "json", "required": True}},
            "parameters": {"type": "object", "additionalProperties": False, "properties": {name: _value_schema(value) for name, value in parameters.items()}, "required": list(parameters)},
            "outputs": {"dataset": {"artifact_type": "json", "path": "/outputs/selection.json", "required": True}},
            "implementation": {"kind": "oci", "source_sha256": "0" * 64, "entrypoint": ["python3", "/method/main.py"]},
            "environment": {"image": IMAGE, "lock_sha256": lock_sha},
            "execution": {"cpu": BOUNDS["candidate_cpu"], "memory_mb": BOUNDS["candidate_memory_mib"],
                          "timeout_seconds": BOUNDS["candidate_seconds"], "pids": 64,
                          "max_output_bytes": BOUNDS["candidate_output_bytes"], "network": "deny"},
            "validation": policy, "provenance": {"origin": "generated_synthetic_pilot"},
            "scientific": {"tasks": ["quality_control"], "assumptions": ["Synthetic observation-level measurements, without independently verified biological quality labels."],
                           "preconditions": [], "implementation_origin": "generated", "references": [],
                           "output_semantics": (
                               'Input /inputs/dataset is JSON {"format":"devbench.qc-observations/1","observations":[...]} with immutable complete rows. '
                               'Output /outputs/selection.json is exactly {"retained_ids":[...]} in original input order, unique and from the input. '
                               "Use candidate-chosen frozen parameters only; no parameter search in this pilot. Python standard library only. "
                               "Public validation metrics are independent of candidate self-reports: for each qc_metrics field, /reference_qc_means/FIELD/after, "
                               "/reference_qc_means/FIELD/absolute_standardized_shift, or /reference_qc_quantiles/FIELD/after/INDEX (INDEX 0..4). "
                               "For example /reference_qc_means/" + example + "/after is a finite scalar for nonempty outputs. "
                               "A shift is absolute mean displacement / original population SD (zero for a constant preserved field). "
                               "Metric directions and tradeoffs need scientific justification; QC shifts alone do not prove technical damage or biological validity. "
                               "Use one joint full_dataset experiment covering every generated eligible candidate, dataset input port, and each candidate's frozen parameter vector. "
                               "Trial budget: memory_mb=512, timeout_seconds between 20 times candidate count and 60, max_runs exactly candidate count. "
                               "Only validation pointers above and runtime elapsed_seconds/output_bytes are admitted. No self-reported score can guide selection.")}}
    scope = candidate.get("action_scope")
    if scope is not None:
        from devharness_core.methods import validate_action_scope
        contract["action_scope"] = validate_action_scope(scope)
    if scope is not None and scope["intent"] == "diagnostic_flagging":
        from devharness_core.methods import diagnostic_reasons
        if diagnostic_profiles:
            descriptor = candidate.get("diagnostic_profile")
            if descriptor is None:
                raise ValueError("Continuous diagnostic implementation unavailable: missing frozen diagnostic_profile descriptor")
            chosen = descriptor.get("measurements")
            if not isinstance(chosen, list) or not chosen or not set(chosen) <= set(measurements):
                raise ValueError("Frozen profile measurements are unavailable in this dataset")
            measurements = chosen
            contract["validation"]["qc_metrics"] = deepcopy(chosen)
        reasons = {status: diagnostic_reasons(scope["interpretation"], status)
                   for status in ("flagged", "not_flagged", "unavailable")}
        semantics = {name: meaning for name, meaning in (measurement_semantics or {}).items() if name in measurements}
        if scope["interpretation"] == "qc_review" and not set(measurements) <= semantics.keys():
            raise ValueError("QC review requires coordinator measurement semantics")
        contract["validation"]["comparator"] = "observation_diagnostic"
        contract["validation"]["diagnostic_policy"] = {
            "format": "devbench.diagnostic-policy/1", "interpretation": scope["interpretation"],
            "measurement_semantics": semantics}
        contract["scientific"]["output_semantics"] = (
            'Input /inputs/dataset is immutable JSON {"format":"devbench.qc-observations/1","observations":[...]}. '
            'Output /outputs/selection.json must contain exactly {"format":"devbench.observation-diagnostics/1",'
            '"input_sha256":<SHA-256 of exact input file bytes>,"evidence_scope":"' + scope["interpretation"] + '",'
            '"retained_ids":[every cell_id in input order],"records":[one record per observation in input order]}. '
            'Each record is exactly {"observation_id":cell_id,"status":"flagged"|"not_flagged"|"unavailable",'
            '"score":finite number or null for unavailable,"reason":bounded nonempty explanation,'
            '"evidence_refs":["/observations/INDEX/MEASUREMENT",...]}. '
            'Available scores require evidence references to declared qc_metrics on that same observation. '
            'Unavailable scores are null with a concrete reason. Preserve ALL identifiers/order/measurements; no removal or transformation. '
            'Reasons must be chosen exactly from the coordinator policy by status: ' + json.dumps(reasons, sort_keys=True) + '. '
            'Numerical atypicality means unusual under this representation; QC review means warrants review, never proven damage or causation. '
            'Python standard library only, candidate-chosen frozen parameters, no parameter search. '
            'Public independent descriptive metrics: /diagnostic_measurements/flag_rate, /diagnostic_measurements/available_fraction, '
            '/diagnostic_measurements/by_measurement/FIELD/input_mean, /diagnostic_measurements/by_measurement/FIELD/flagged_mean, '
            'or /diagnostic_measurements/by_measurement/FIELD/absolute_standardized_flagged_shift. '
            'No hidden labels or self-reported accuracy; these diagnostics alone cannot establish a comparative winner. '
            'Declare one joint full_dataset experiment covering all implemented eligible candidates and their frozen parameters. '
            'Trial memory_mb=512, timeout_seconds between 20 times candidate count and 60, max_runs exactly candidate count.')
        if diagnostic_profiles:
            from devharness_core.methods import validate_diagnostic_policy
            declared = {**contract["validation"]["diagnostic_policy"],
                        "format": "devbench.diagnostic-policy/2", "profile": deepcopy(descriptor)}
            declared = validate_diagnostic_policy(declared)
            if not set(descriptor["group_by"]) <= ({"group", "sample"} if has_samples else {"group"}):
                raise ValueError("Frozen profile reference grouping is unavailable for this dataset")
            contract["validation"]["diagnostic_policy"] = declared
            contract["scientific"]["output_semantics"] = profile_output_semantics(declared)
    return contract


def profile_output_semantics(policy):
    from devharness_core.methods import diagnostic_reasons
    return (
        'Input /inputs/dataset is immutable complete devbench.qc-observations/1 JSON. '
        'Output /outputs/selection.json is exactly {"format":"devbench.observation-diagnostics/2",'
        '"input_sha256":SHA-256 of exact input bytes,"evidence_scope":"' + policy["interpretation"] + '",'
        '"retained_ids":all cell_ids in input order,"records":one record per input row in order}. '
        'Every record is exactly {"observation_id":cell_id,"score":finite number or null,"score_reason":null if available else "Profile score unavailable",'
        '"flag":true|false|null,"flag_reason":canonical reason,"profiles":{MEASUREMENT:{"score":finite number or null,"reason":null if available else concrete reason,'
        '"reference_count":group size,"reference_group":{grouping field:row label}}},"evidence_refs":["/observations/INDEX/MEASUREMENT",...]}. '
        'Preserve all immutable input rows. Cite every chosen measurement on that same row, escaping JSON pointer tokens. '
        'Use exactly the scientifically frozen definition ' + json.dumps(policy["profile"], sort_keys=True) + '. '
        'References include every row in the exact group_by group; [] means all rows. Compute each measurement even if another is unavailable. '
        'absolute_mad = abs(value - median(reference values)) / median(abs(reference value - reference median)), with no normalizing constant. '
        'Zero denominator => score null, reason "Zero MAD in reference". Nonfinite calculation => null, reason "Nonfinite score calculation". '
        'empirical_two_tail = 1 - 2 * min(midrank, 1-midrank), where midrank=(count(reference < value)+0.5*count(reference == value))/reference_count. '
        'Ties use this midrank; a constant reference gives valid zero scores. '
        'Aggregate max is available only when every chosen measurement is computable; otherwise aggregate null, retaining computable measurement profiles. '
        'A null frozen flag_threshold means EVERY flag is null with reason "Flag threshold unresolved", even for finite scores. '
        'With finite threshold, unavailable aggregate gives flag null and reason "Profile score unavailable"; otherwise flag=(score>=threshold). '
        'Decided true/false reasons respectively are ' + json.dumps([diagnostic_reasons(policy["interpretation"], status)[0]
                                                                    for status in ("flagged", "not_flagged")]) + '. '
        'Python standard library only, frozen parameters, no parameter search, family substitution or threshold invention. '
        'Public independent numerical profiles are descriptive: /profile_metrics/aggregate_mean, /profile_metrics/aggregate_max, '
        '/profile_metrics/by_measurement/FIELD/available_fraction, /mean_score, /max_score; score coverage /diagnostic_measurements/available_fraction, '
        'decision coverage /diagnostic_measurements/decision_fraction; conditional /diagnostic_measurements/flag_rate is unavailable with no decisions. '
        'Common immutable dataset dimensions /shape/before/0 and /shape/before/1 are descriptive only. '
        'For mixed output kinds use only common input shape or runtime metrics; retention metrics cannot describe profiles. '
        'No hidden labels, self-reported accuracy, biological damage or causation claims. Numerical fidelity or coverage alone cannot establish a scientific winner. '
        'Declare one joint full_dataset experiment covering all implemented eligible candidates and frozen parameters. '
        'Trial memory_mb=512, timeout_seconds between 20 times candidate count and 60, max_runs exactly candidate count.')


def admit_trial(spec, candidates, artifact_id, measurements, *, diagnostic_profiles=False):
    names = [row["name"] for row in candidates]
    if (len(names) not in (1, 2) or spec["candidates"] != names or spec["subset_spec"].get("kind") != "full_dataset"
            or spec["inputs"] != {name: {"dataset": artifact_id} for name in names}
            or spec["parameters"] != {row["name"]: frozen_parameters(row) for row in candidates}):
        raise ValueError("Trial must preserve frozen candidates, complete immutable inputs and candidate-selected parameters")
    limits = spec["resource_limits"]
    if (limits["max_runs"] != len(names) or limits["memory_mb"] != BOUNDS["candidate_memory_mib"]
            or not BOUNDS["candidate_seconds"] * len(names) <= limits["timeout_seconds"] <= 60):
        raise ValueError("Trial exceeds the fixed pilot resource budget")
    allowed = set.intersection(*(set(metric_catalog(row["diagnostic_profile"]["measurements"] if diagnostic_profiles and "diagnostic_profile" in row else measurements,
        diagnostics=row.get("action_scope", {}).get("intent") == "diagnostic_flagging",
        profiles=diagnostic_profiles and "diagnostic_profile" in row and row.get("action_scope", {}).get("intent") == "diagnostic_flagging"))
        for row in candidates))
    for source in spec["metric_sources"].values():
        if (source.get("kind") == "validation" and source.get("field") in allowed
                or source.get("kind") == "runtime" and source.get("field") in {"elapsed_seconds", "output_bytes"}):
            continue
        raise ValueError("Trial metric is outside the public coordinator diagnostic catalog")


def require_authorization(root, *, allow_model, allow_data):
    if allow_model is not True or allow_data is not True:
        raise PermissionError("The bounded pilot needs explicit model and synthetic-data execution flags")
    require_live_open(root)


def verify_source(root, source_manifest):
    root, source_manifest = _path(root), _path(source_manifest)
    manifest = finite_json(source_manifest)
    expected_source = _path(manifest["capture"]["snapshot_root"])
    if SOURCE != expected_source:
        raise ValueError("Run the pilot script from the exact frozen source snapshot")
    for name, row in manifest["files"].items():
        if file_digest(SOURCE / name) != row["sha256"]:
            raise ValueError("Frozen source changed: " + name)
    runtime = finite_json(source_manifest.with_name(source_manifest.name.replace("-source-manifest.json", "-runtime-before.json")))
    packages = dict(sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions() if d.metadata.get("Name")))
    if packages != runtime["packages"]:
        raise ValueError("Runtime package versions changed since source qualification")
    return {"manifest_sha256": file_digest(source_manifest), "source_files": len(manifest["files"]),
            "source_manifest_content_sha256": manifest["capture"]["manifest_sha256"], "runtime_packages": len(packages)}


def pilot_runtime(root):
    """Use only the executable identity sealed with this cycle's preparation."""
    from devbench.study.cli_identity import verify_codex_identity
    root = _path(root)
    name = "private/evaluator/runtime.json"
    preparation = finite_json(root / "pilot-manifest.json")
    if name not in preparation["files"]:
        raise ValueError("Codex runtime identity was not frozen with this preparation")
    if file_digest(root / name) != preparation["files"][name]:
        raise ValueError("Frozen Codex runtime identity changed")
    identity = finite_json(root / name)
    executable = _path(identity["executable_path"])
    if (str(executable) not in identity["component_sha256"]
            or identity["component_sha256"][str(executable)] != identity["executable_sha256"]):
        raise ValueError("Pinned Codex executable identity is inconsistent")
    if any(_path(path).is_relative_to(root) for path in identity["component_sha256"]):
        raise ValueError("Codex runtime must remain outside the evaluator private root")
    verify_codex_identity(identity)
    return identity


def _remaining(root):
    started = finite_json(root / "live-start.json")["monotonic"]
    elapsed = time.monotonic() - started
    if elapsed < 0 or elapsed >= BOUNDS["total_live_wall_seconds"]:
        raise TimeoutError("Total live pilot wall-time budget exhausted")
    return BOUNDS["total_live_wall_seconds"] - elapsed


def _reserve_phase(root, identity, phase):
    require_live_open(root)
    if (root / "evidence/decisions" / (identity + ".json")).exists():
        raise ValueError("A sealed case cannot receive further model dispatch")
    directory = root / "private/dispatch-reservations"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    existing = list(directory.glob("*.json"))
    if len(existing) >= BOUNDS["max_selections_total"] or sum(path.name.startswith(identity + "-") for path in existing) >= BOUNDS["max_selections_per_case"]:
        raise ValueError("Native selection reservation budget exhausted")
    remaining = _remaining(root)
    write_once(directory / (identity + "-" + phase + ".json"), {"case_id": identity, "phase": phase,
        "maximum_provider_sessions_including_preflight": 2, "remaining_total_wall_seconds": remaining,
        "reservation_before_dispatch": True})


def offline_boundary_check(root, expected_digest, source_manifest, actor_parent):
    """Probe the native sandbox with harmless files; no provider or method run."""
    from devbench.staging import stage_python
    from devbench.study.execution import _runtime_roots
    from devbench.study.permissions import PermissionBoundary, probe_boundary
    from devbench.analysis.method_executor import container_command
    root, actor_parent = _path(root), _path(actor_parent)
    manifest = verify_preparation(root, expected_digest)
    source = verify_source(root, source_manifest)
    identity = pilot_runtime(root)
    executable = identity["executable_path"]
    if actor_parent.is_relative_to(root) or root.is_relative_to(actor_parent):
        raise ValueError("Actor and evaluator boundaries must be disjoint")
    workspace = actor_parent / "probe-only"
    sibling = actor_parent / "probe-sibling"
    workspace.mkdir(mode=0o700, parents=True)
    sibling.mkdir(mode=0o700)
    (workspace / "control").mkdir(mode=0o700)
    environment = stage_python(workspace, assisted=False)
    boundary = PermissionBoundary(workspace=workspace, private_roots=(root, sibling),
        runtime_roots=_runtime_roots(workspace, environment),
        protected_paths=(workspace / "control", workspace / ".venv"))
    probe = probe_boundary(boundary, executable, Path(environment["python"]), allow_configuration_probe=True)
    if not probe["verified"]:
        write_once(root / "evidence/offline-boundary-failed.json", probe)
        raise RuntimeError("Native private-root configuration probe failed")
    contract = interface_for({"name": "Boundary fixture only", "parameter_candidates": []}, ["quality_a"], False, "0" * 64)
    command = container_command(contract, root / "private/boundary-example-run", "devharness-boundary-only")
    mounts = [command[index + 1] for index, value in enumerate(command) if value == "--mount"]
    if len(mounts) != 2 or any("evaluator" in mount for mount in mounts):
        raise ValueError("Unexpected OCI host mount")
    evidence = {"verified": True, "model_calls": 0, "candidate_runs": 0, "source": source,
        "private_root": str(root), "reference_path_under_denied_root": True,
        "native_configuration_probe": probe, "codex_identity": identity,
        "oci_mount_template": mounts, "oci_network_none": "--network=none" in command,
        "candidate_input_scope": "Each actual run binds only the imported dataset artifact; no reference, rubric or score artifacts are imported.",
        "case_count": len(manifest["case_ids"]),
        "limits": "Offline profile proof. Every native selector action must additionally pass its actual provider preflight using the same denied private root."}
    write_once(root / "evidence/offline-boundary.json", evidence)
    return {"verified": True, "model_calls": 0, "candidate_runs": 0}


def live_case(root, identity, expected_digest, source_manifest, actor_parent, *, allow_model=False, allow_data=False):
    from devbench.analysis.episode_agent import AgentLimits
    from devbench.analysis.episode_codex import CodexActionSelector
    from devbench.analysis.method_planner import ScientificMethodPlanner
    from devbench.analysis.methods import MethodService
    root, actor_parent = _path(root), _path(actor_parent)
    require_authorization(root, allow_model=allow_model, allow_data=allow_data)
    manifest = verify_preparation(root, expected_digest)
    source_identity = verify_source(root, source_manifest)
    runtime_identity = pilot_runtime(root)
    if identity not in manifest["case_ids"] or actor_parent.is_relative_to(root) or root.is_relative_to(actor_parent):
        raise ValueError("Use a known opaque case and separate protected actor parent")
    case_dir = root / "private/cases" / identity
    write_once(case_dir / "attempt-started.json", {"case_id": identity, "source": source_identity,
               "freeze": expected_digest, "automatic_retry": False})
    if not (root / "live-start.json").exists():
        write_once(root / "live-start.json", {"monotonic": time.monotonic(), "bounds": BOUNDS})
    _remaining(root)
    actor_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    public = public_case(root, identity)
    methods = MethodService(case_dir / "service")
    planner = ScientificMethodPlanner(methods)
    artifact = methods.import_artifact(case_dir / "dataset.json", artifact_type="json", actor=ACTOR)
    public["dataset_summary"]["artifact_ids"] = [artifact["artifact_id"]]
    plan = planner.create(public["task"], public["dataset_summary"], actor=ACTOR,
        evaluation_purpose="method_selection", user_parameter_requirements=[], data_classification="synthetic",
        limits=AgentLimits(max_selections=BOUNDS["max_selections_per_case"], timeout_seconds=BOUNDS["case_model_seconds"],
                           session_timeout_seconds=BOUNDS["selection_seconds"], memory_mb=BOUNDS["selector_memory_mib"], max_context_bytes=BOUNDS["max_context_bytes"]))
    session = plan["session_id"]
    analysis = methods.create_analysis("Opaque synthetic pilot " + identity, actor=ACTOR)
    branch = methods.create_branch(analysis["analysis_id"], analysis["revision_id"], actor=ACTOR)

    class DeadlineSelector(CodexActionSelector):
        def select(self, context, output, *, timeout_seconds, memory_mb=None, allow_model_execution=False):
            # Preserve native staging, permissions, provider preflight and action;
            # this wrapper only narrows the already bounded remaining deadline.
            return super().select(context, output, timeout_seconds=min(timeout_seconds, _remaining(root)),
                                  memory_mb=memory_mb, allow_model_execution=allow_model_execution)

    selector = DeadlineSelector(requested_model=BOUNDS["requested_model"], reasoning_effort=BOUNDS["reasoning_effort"],
        workspace_parent=actor_parent, private_root=root, executable=runtime_identity["executable_path"], memory_mb=BOUNDS["selector_memory_mib"])
    operations, accepted, terminal = [], False, "started"

    def call(phase, operation):
        verify_preparation(root, expected_digest)
        pilot_runtime(root)
        _reserve_phase(root, identity, phase)
        started = time.monotonic()
        print(json.dumps({"case_id": identity, "phase": phase, "status": "starting"}), flush=True)
        try:
            value = operation()
            operations.append({"phase": phase, "status": "completed", "elapsed_seconds": time.monotonic() - started})
            return value
        except Exception as error:
            operations.append({"phase": phase, "status": "failed", "elapsed_seconds": time.monotonic() - started,
                               "error": f"{type(error).__name__}: {error}"[:2000]})
            raise
        finally:
            write_once(case_dir / "operations" / (phase + ".json"), operations[-1])
            print(json.dumps({"case_id": identity, **operations[-1]}), flush=True)

    try:
        terminal = "scientific_planning_failed"
        candidates = call("science", lambda: planner.generate_candidates(session, selector, actor=ACTOR, allow_model_execution=True))
        write_once(case_dir / "scientific-candidates-frozen.json", {"candidates": candidates, "sha256": digest(candidates),
                   "registry_exposure_occurred": False})
        terminal = "capability_resolution_failed"
        resolved = call("resolve", lambda: planner.resolve_capabilities(session, selector, [], actor=ACTOR, allow_model_execution=True))
        write_once(case_dir / "capability-exposure.json", {"registry": {}, "candidates_sha256": digest(candidates),
                   "science_selection_preceded_exposure": True})
        # The deterministic allocation is frozen before outcomes and preserves
        # all other candidates as untested, never as scientifically inferior.
        eligible = [row for row in resolved if row["applicability"] == "eligible"][:2]
        measurements = public["dataset_summary"]["observed"]["measurement_names"]
        lock = case_dir / "environment.lock.json"
        lock_sha = write_once(lock, {"image": IMAGE, "runtime": "Python standard library supplied by the installed digest-pinned image",
                                   "network": "deny", "no_installation": True})
        generated = []
        for number, candidate in enumerate(eligible, 1):
            try:
                rubric = finite_json(root / "private/evaluator/rubric.json")
                if (candidate.get("action_scope", {}).get("intent") == "diagnostic_flagging"
                        and rubric not in (DIAGNOSTIC_RUBRIC, PROFILE_RUBRIC)):
                    raise ValueError("Diagnostic outputs require a separately frozen v2 or v3 rubric")
                interface = interface_for(candidate, measurements, public["dataset_summary"]["observed"]["sample_labels_present"], lock_sha,
                    measurement_semantics=public["dataset_summary"]["observed"].get("declared_measurement_meanings", {}),
                    diagnostic_profiles=rubric == PROFILE_RUBRIC)
                call("draft" + str(number), lambda candidate=candidate, interface=interface: planner.draft_implementation(
                    session, candidate["name"], selector, interface, lock, actor=ACTOR, allow_model_execution=True,
                    justification="No vetted matching implementation was supplied. Author this candidate only as explicitly experimental code using the immutable interface and Python standard library. Read /inputs/dataset and /method/parameters.json; write the exact declared output_semantics JSON at /outputs/selection.json. Use the scientific candidate's frozen parameter choices, with no tuning or ground-truth access. No primary sources were retrieved, so source_references must be empty. This is not faithful reproduction or validation of an external package."))
                generated.append(candidate)
            except Exception as error:
                # No retry/repair of an invalid draft. Another already allocated
                # scientific candidate may still be attempted once.
                if not any(row["phase"] == "draft" + str(number) for row in operations):
                    operations.append({"phase": "draft" + str(number), "status": "not_dispatched", "error": f"{type(error).__name__}: {error}"[:2000]})
        if generated:
            terminal = "trial_design_or_admission_failed"
            try:
                proposals = call("design", lambda: planner.design_trials(session, selector, actor=ACTOR, allow_model_execution=True))
                if len(proposals) > 1:
                    raise ValueError("One joint experiment is the predeclared pilot limit")
                if proposals:
                    spec = proposals[0]
                    admit_trial(spec, generated, artifact["artifact_id"], measurements,
                                diagnostic_profiles=finite_json(root / "private/evaluator/rubric.json") == PROFILE_RUBRIC)
                    planner.declare_trial(session, branch["branch_id"], spec, actor=ACTOR)
                    if _remaining(root) < spec["resource_limits"]["timeout_seconds"] + 30:
                        raise TimeoutError("Insufficient remaining live budget for the frozen trial and cleanup")
                    existing_runs = sum(len(MethodService(path, readonly=True).list_records("run")) for path in (root / "private/cases").glob("*/service"))
                    if existing_runs + len(generated) > BOUNDS["max_candidate_runs"]:
                        raise ValueError("Candidate execution budget exhausted")
                    results = planner.run_trial(session, spec["id"], actor=ACTOR, allow_data_execution=True)
                    write_once(case_dir / "actual-trial-results.json", results)
                    operations.append({"phase": "trial", "status": "completed", "candidate_outcomes": [{"candidate": row["candidate"], "status": row["status"]} for row in results]})
                else:
                    operations.append({"phase": "trial", "status": "not_proposed"})
            except Exception as error:
                operations.append({"phase": "trial", "status": "not_executed_or_failed", "error": f"{type(error).__name__}: {error}"[:2000]})
        terminal = "recommendation_failed"
        call("recommend", lambda: planner.recommend(session, selector, actor=ACTOR, allow_model_execution=True))
        accepted, terminal = True, planner.terminal_outcome(session)
    except Exception as error:
        operations.append({"phase": "terminal", "status": terminal, "error": f"{type(error).__name__}: {error}"[:2000]})
    state = planner.get(session)
    # Verify the current native records before sealing any output identity.
    planner.evidence_assessment(session)
    outputs = []
    for row in state["results"]:
        run_id = row["provenance"].get("run_id")
        if not run_id:
            continue
        run = methods.get("run", run_id)
        if run["status"] != "succeeded" or row["status"] not in {"success", "partial"}:
            continue
        if run["inputs"] != {"dataset": artifact["artifact_id"]} or not methods.provenance(run_id)["valid"]:
            raise ValueError("Executed method input/receipt differs from the frozen case")
        output_id = run["outputs"]["dataset"]
        path = methods.artifact_path(output_id)
        outputs.append({"candidate": row["candidate"], "run_id": run_id, "status": "success", "trial_status": row["status"],
                        "path": str(path), "sha256": file_digest(path), "output_artifact_id": output_id,
                        "input_artifact_id": artifact["artifact_id"], "receipt_root": run["evidence"]["root_hash"]})
    write_once(case_dir / "operations-final.json", operations)
    source_after = verify_source(root, source_manifest)
    verify_preparation(root, expected_digest)
    sealed = seal_decision(root, identity, state["recommendation"], accepted=accepted, terminal_status=terminal, outputs=outputs, state=state)
    native_evidence = [finite_json(path) for path in (methods.root / "planner-selections" / session).glob("*/evidence.json")]
    summary = {"case_id": identity, "session_id": session, "terminal_status": terminal, "recommendation_accepted": accepted,
               "decision_sha256": sealed, "output_count": len(outputs), "source_after": source_after,
               "selection_attempts": len(state["selection_history"]),
               "provider_sessions_attempted": sum(row.get("model_sessions_attempted", 0) for row in native_evidence),
               "observed_models": sorted({row["actual_model"] for row in native_evidence if row.get("actual_model")}),
               "tokens_reported": sum(row["tokens"] for row in native_evidence) if all(type(row.get("tokens")) is int for row in native_evidence) else None,
               "cost_usd": None,
               "actual_runs": len(methods.list_records("run"))}
    write_once(case_dir / "case-summary.json", summary)
    print(json.dumps(summary), flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["prepare", "boundary-check", "run-case", "grade"])
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--design", type=Path)
    parser.add_argument("--case")
    parser.add_argument("--expected-freeze")
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--actor-parent", type=Path)
    parser.add_argument("--codex-executable", type=Path, help="Canonical installed Codex executable to pin during preparation")
    parser.add_argument("--allow-model-execution", action="store_true")
    parser.add_argument("--allow-data-execution", action="store_true")
    parser.add_argument("--diagnostic-outputs", action="store_true", help="Freeze additive diagnostic rubric and pre-reference blinded review gate")
    parser.add_argument("--diagnostic-profiles", action="store_true", help="Freeze v3 numerical profile rubric with separate score fidelity and categorical decision coverage")
    args = parser.parse_args()
    root = _path(args.report)
    if args.mode == "prepare":
        if args.codex_executable is None:
            parser.error("prepare requires an explicit --codex-executable to freeze the native runtime")
        from devbench.study.cli_identity import read_codex_identity
        runtime = read_codex_identity(_path(args.codex_executable))
        design = finite_json(_path(args.design)) if args.design else None
        value = prepare_pilot(root, seed=secrets.randbits(96), design=design, runtime=runtime,
                              diagnostic_outputs=args.diagnostic_outputs, diagnostic_profiles=args.diagnostic_profiles)
        print(json.dumps({"case_ids": value["case_ids"], "content_sha256": value["content_sha256"]}), flush=True)
    elif args.mode == "boundary-check":
        if not all((args.expected_freeze, args.source_manifest, args.actor_parent)):
            parser.error("boundary-check requires the input freeze, source manifest and actor parent")
        print(json.dumps(offline_boundary_check(root, args.expected_freeze, args.source_manifest, args.actor_parent)), flush=True)
    elif args.mode == "run-case":
        if not all((args.case, args.expected_freeze, args.source_manifest, args.actor_parent)):
            parser.error("run-case requires exact case, input freeze, source manifest and actor parent")
        live_case(root, args.case, args.expected_freeze, args.source_manifest, args.actor_parent,
                  allow_model=args.allow_model_execution, allow_data=args.allow_data_execution)
    else:
        if not args.expected_freeze or not args.source_manifest:
            parser.error("grade requires the retained input freeze and source manifest")
        verify_source(root, args.source_manifest)
        manifest = finite_json(root / "pilot-manifest.json")
        seals = {identity: finite_json(root / "private/cases" / identity / "case-summary.json")["decision_sha256"] for identity in manifest["case_ids"]}
        result = evaluate_pilot(root, args.expected_freeze, seals)
        plots = plot_pilot(root, result)
        if finite_json(root / "private/evaluator/rubric.json")["format"] in {"devbench.synthetic-qc-rubric/2", "devbench.synthetic-qc-rubric/3"}:
            plots = {"prepared": plots, "executed": plot_executed_outputs(root, result)}
        write_once(root / "plot-manifest.json", plots)
        print(json.dumps({"scored_cases": len(result["cases"]), "plots": plots}), flush=True)


if __name__ == "__main__":
    main()
