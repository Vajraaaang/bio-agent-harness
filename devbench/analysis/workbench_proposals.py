"""Reviewed dispatch of the existing backed workbench; no new scientific engine.

Large expression inputs remain external. Review is caller-asserted research
review and never grants execution permission, model access or clinical release.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import time
import uuid

from .checkpoints import atomic_json, digest, sha256
from .episode_proposals import (_absolute, _retained, _seal, _load, _copy_proof,
    _review_decision, require_disjoint_output, _SCOPE)
from .episodes import _json_copy, _public_only

_CONFIRMATIONS = {"question_and_scope", "input_provenance", "capture_units", "settings_and_limits", "interpretation_limits"}
_DECLARATIONS = {"assay", "measurement", "count_provenance", "capture_unit_key", "capture_units_confirmed", "annotation_provenance"}


def _source_identity():
    from scripts.build_colab_notebook import default_source_bundle
    return default_source_bundle()["sha256"]


def _configuration(question, parameters, declarations, timeout):
    from .workbench import _validated_parameters
    if not isinstance(question, str) or not question.strip() or len(question) > 8000:
        raise ValueError("A bounded biological question is required")
    parameters = _validated_parameters(_json_copy(parameters))
    if {"source_sha256", "review_binding_sha256"} & parameters.keys() or parameters["parent_root"] is not None:
        raise ValueError("Prepare a fresh workbench proposal; replay uses the native bundle path")
    declarations = _json_copy(declarations)
    _public_only(declarations)
    if not isinstance(declarations, dict) or set(declarations) != _DECLARATIONS:
        raise ValueError("Declare assay, measurement, count provenance, capture units and annotation provenance")
    if type(declarations["capture_units_confirmed"]) is not bool:
        raise ValueError("Capture identity confirmation must be an explicit boolean")
    if any(not isinstance(declarations[key], str) or not declarations[key].strip() or len(declarations[key]) > 4000
           for key in _DECLARATIONS - {"capture_units_confirmed"}):
        raise ValueError("Use bounded nonempty workbench provenance declarations")
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 3600:
        raise ValueError("Reviewed workbench deadline must be positive and no more than 3600 seconds")
    eligible = (declarations["assay"] == "scrna" and declarations["measurement"] == "umi_counts"
                and declarations["capture_units_confirmed"]
                and declarations["capture_unit_key"] == parameters["batch_key"])
    return {"question": question, "parameters": parameters, "declarations": declarations,
            "run_timeout_seconds": timeout}, eligible


def _inspection_worker(source, parameters, model_path):
    from .resources import configure_threads
    configure_threads()
    from .runtime import verify_data_identity, observe_runtime
    from .workbench import resource_plan
    identity = verify_data_identity(Path(source), parameters["data_sha256"])
    model = None
    if parameters["model_path"] is not None:
        if model_path is None or sha256(model_path) != parameters["model_sha256"]:
            raise ValueError("model_checksum_mismatch")
        model = {"path": str(model_path), "sha256": parameters["model_sha256"]}
    elif model_path is not None:
        raise ValueError("Undeclared model input")
    preview = resource_plan(Path(source), parameters)
    return {"external_input": {"path": source, "sha256": identity["data_sha256"],
                              "size_bytes": identity["bytes"], "copied_into_proposal": False},
            "model_input": model, "preview": preview,
            "runtime": observe_runtime(parameters["execution_tier"], detect_gpu=False)}


def _proposal_record(config, eligible, inspected, source_id, diagnostics):
    return {"format": "devbench.workbench-proposal/1", "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "configuration": config, **inspected, "source_bundle_sha256": source_id,
        "inspection_supervision": diagnostics, "supported_workflow": bool(eligible),
        "status": "awaiting_operator_review" if eligible else "needs_information",
        "admission_scope": "supplied_declarations_and_input_identity_not_independent_scientific_validation",
        "clinical_release_allowed": False, "data_execution_granted": False, "model_execution_granted": False}


def prepare_workbench_proposal(output, *, question, source, parameters, declarations,
                               model_path=None, run_timeout_seconds=3600, allow_data_read=False):
    if allow_data_read is not True:
        raise PermissionError("Workbench proposal preparation requires explicit data-reading authorization")
    config, eligible = _configuration(question, parameters, declarations, run_timeout_seconds)
    source = _absolute(source)
    model_path = _absolute(model_path) if model_path is not None else None
    output = require_disjoint_output(output, [source, *([model_path] if model_path else [])])
    from devbench.study.permissions import require_protected_storage
    require_protected_storage(output)
    if output.exists():
        raise ValueError("Use a fresh proposal directory")
    from devharness_core.watchdog import run_sandboxed
    diagnostics = {}
    source_id = _source_identity()
    try:
        inspected = run_sandboxed(_inspection_worker, (str(source), config["parameters"], str(model_path) if model_path else None),
            timeout_sec=min(config["parameters"]["stage_timeout_seconds"], run_timeout_seconds),
            max_mem_mb=config["parameters"]["memory_mb"], diagnostics=diagnostics,
            growth_window_seconds=config["parameters"].get("growth_window_seconds", 0))
        if _source_identity() != source_id:
            raise ValueError("Workbench implementation changed during inspection")
    except (Exception, KeyboardInterrupt) as error:
        _seal(output, "failure.json", {"format": "devbench.workbench-proposal-failure/1",
            "status": "infrastructure_failure", "configuration": config, "input_path": str(source),
            "source_bundle_sha256": source_id, "inspection_supervision": diagnostics,
            "error_type": type(error).__name__, "reason": str(error)[:2000],
            "analysis_executed": False, "clinical_release_allowed": False}, "workbench.proposal_failed")
        raise
    record = _proposal_record(config, eligible, inspected, source_id, diagnostics)
    return _seal(output, "proposal.json", record, "workbench.proposed")


def _verify_workbench_authoring(record, authoring):
    from .episode_proposals import _authoring_records
    from .episode_planning import proposal_schema
    import jsonschema
    records = _authoring_records(authoring)
    request = records["request.json"]
    selection = records["selection.json"]["decision"]
    context = records["context.json"]
    jsonschema.Draft202012Validator(proposal_schema(context)).validate(selection)
    if (selection.get("kind") != "propose" or selection.get("workflow") != record["configuration"]["parameters"]["workflow"]
            or context.get("workflow_family") != "backed_workbench"):
        raise ValueError("Workbench authoring does not select the recorded workflow")
    unsigned = {key: value for key, value in record.items() if key != "authoring"}
    if records["compiled-spec.json"] != unsigned:
        raise ValueError("Workbench proposal differs from its authored record")
    if (request.get("workbench_configuration") != record["configuration"]
            or request.get("source_sha256") != record["source_bundle_sha256"]
            or request.get("source_path") != record["external_input"]["path"]
            or request.get("model_path") != (record["model_input"] or {}).get("path")):
        raise ValueError("Workbench authoring identity changed")


def _publish_authored_workbench(output, record, authoring):
    _current(record, check_data=True)
    _verify_workbench_authoring(record, authoring)
    require_disjoint_output(output, [record["external_input"]["path"],
        *([record["model_input"]["path"]] if record["model_input"] else [])])
    return _seal(output, "proposal.json", {**record, "authoring": _json_copy(authoring)}, "workbench.proposed")


def _current(record, *, check_data):
    if record.get("format") != "devbench.workbench-proposal/1":
        raise ValueError("Use a workbench proposal")
    config, eligible = _configuration(record["configuration"]["question"], record["configuration"]["parameters"],
        record["configuration"]["declarations"], record["configuration"]["run_timeout_seconds"])
    if config != record["configuration"] or eligible != record["supported_workflow"]:
        raise ValueError("Workbench proposal configuration changed")
    if (record["external_input"].get("sha256") != config["parameters"]["data_sha256"]
            or record["external_input"].get("copied_into_proposal") is not False
            or any(record.get(key) is not False for key in (
                "clinical_release_allowed", "data_execution_granted", "model_execution_granted"))):
        raise ValueError("Workbench proposal has inconsistent identity or authority claims")
    if check_data:
        from .runtime import verify_data_identity
        verify_data_identity(_absolute(record["external_input"]["path"]), config["parameters"]["data_sha256"])
        if _source_identity() != record["source_bundle_sha256"]:
            raise ValueError("Workbench source identity changed")
        if record["model_input"] is not None and sha256(_absolute(record["model_input"]["path"])) != config["parameters"]["model_sha256"]:
            raise ValueError("Workbench model identity changed")
        if "authoring" in record:
            _verify_workbench_authoring(record, record["authoring"])
    return config


def record_workbench_review(output, proposal_manifest, *, expected_root, reviewer, decision,
                            rationale, confirmations, allow_review=False):
    if allow_review is not True:
        raise PermissionError("Recording workbench review requires explicit operator authorization")
    _review_decision(decision, reviewer, rationale, confirmations, required_confirmations=_CONFIRMATIONS)
    proposal_root, record, _ = _load(proposal_manifest, expected_root, "proposal.json")
    _current(record, check_data=decision == "approve")
    inputs = [proposal_root, record["external_input"]["path"]]
    if record["model_input"]:
        inputs.append(record["model_input"]["path"])
    require_disjoint_output(output, inputs)
    if decision == "approve" and record["supported_workflow"] is not True:
        raise ValueError("Unsupported or unconfirmed capture/assay declarations are not eligible for approval")
    review = {"format": "devbench.workbench-review/1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "proposal_root": expected_root,
        "proposal_sha256": digest(record), "reviewer_label": reviewer, "decision": decision,
        "rationale": rationale, "confirmations": dict(confirmations), "review_scope": _SCOPE,
        "clinical_release_allowed": False, "data_execution_granted": False, "model_execution_granted": False}
    if "authoring" in record and decision == "approve":
        review["authoring_root_verified"] = record["authoring"]["root_hash"]
    return _seal(output, "review.json", review, "workbench.reviewed")


def _approved(proposal_manifest, review_manifest, proposal_root, review_root):
    p_root, proposal, _ = _load(proposal_manifest, proposal_root, "proposal.json")
    r_root, review, _ = _load(review_manifest, review_root, "review.json")
    _current(proposal, check_data=False)
    if (review.get("format") != "devbench.workbench-review/1" or review.get("proposal_root") != proposal_root
            or review.get("proposal_sha256") != digest(proposal)):
        raise ValueError("Workbench review does not bind this proposal")
    _review_decision(review["decision"], review["reviewer_label"], review["rationale"], review["confirmations"],
                     required_confirmations=_CONFIRMATIONS)
    if review["decision"] != "approve" or proposal["supported_workflow"] is not True:
        raise ValueError("An approved supported workbench proposal is required")
    if "authoring" in proposal and review.get("authoring_root_verified") != proposal["authoring"]["root_hash"]:
        raise ValueError("Workbench review does not bind verified authoring evidence")
    if review.get("review_scope") != _SCOPE or any(review.get(key) is not False for key in (
            "clinical_release_allowed", "data_execution_granted", "model_execution_granted")):
        raise ValueError("Workbench review has unsupported authority claims")
    return p_root, r_root, proposal


def verify_workbench_review(root, parameters):
    """Embedded proof verification; never follows original data/model paths."""
    from devbench.study.evidence import _read, decode_json
    root = _absolute(root)
    expected = parameters.get("review_binding_sha256")
    _retained(expected)
    content = _read(root / "review-binding.json", 1_000_000)
    if hashlib.sha256(content).hexdigest() != expected:
        raise ValueError("Workbench review binding changed")
    binding = decode_json(content)
    if binding.get("format") != "devbench.workbench-review-binding/1":
        raise ValueError("Invalid workbench review binding")
    if binding.get("review_scope") != _SCOPE or binding.get("clinical_release_allowed") is not False:
        raise ValueError("Invalid workbench review scope")
    _, _, proposal = _approved(root / "plan-review/proposal/audit/manifest.json", root / "plan-review/review/audit/manifest.json",
        binding["proposal_root"], binding["review_root"])
    # A native replay can declare its new tier and parent; science/resources
    # remain the reviewed values. Tier is not inherited host attestation.
    actual = {key: value for key, value in parameters.items() if key not in {
        "source_sha256", "review_binding_sha256", "execution_tier", "parent_root"}}
    intended = {key: value for key, value in proposal["configuration"]["parameters"].items()
                if key not in {"execution_tier", "parent_root"}}
    if actual != intended or parameters["source_sha256"] != proposal["source_bundle_sha256"]:
        raise ValueError("Reviewed workbench configuration changed")
    if sha256(root / "source.zip") != proposal["source_bundle_sha256"]:
        raise ValueError("Reviewed workbench source archive changed")
    return {**binding, "run_timeout_seconds": proposal["configuration"]["run_timeout_seconds"]}


def prepare_reviewed_workbench(output, proposal_manifest, review_manifest, *, expected_proposal_root,
                               expected_review_root, allow_data_execution=False):
    if allow_data_execution is not True:
        raise PermissionError("Reviewed workbench preparation requires data-execution authorization")
    p_root, r_root, proposal = _approved(proposal_manifest, review_manifest, expected_proposal_root, expected_review_root)
    config = _current(proposal, check_data=True)
    inputs = [p_root, r_root, proposal["external_input"]["path"]]
    if proposal["model_input"]:
        inputs.append(proposal["model_input"]["path"])
    root = require_disjoint_output(output, inputs)
    if root.exists():
        raise ValueError("Use a fresh reviewed workbench directory")
    binding = {"format": "devbench.workbench-review-binding/1", "proposal_root": expected_proposal_root,
               "review_root": expected_review_root, "review_scope": _SCOPE, "clinical_release_allowed": False}
    from devharness_core.provenance import canonical_json
    binding_bytes = canonical_json(binding)
    parameters = {**config["parameters"], "review_binding_sha256": hashlib.sha256(binding_bytes).hexdigest()}
    from .workbench import prepare_bundle
    try:
        prepared = prepare_bundle(proposal["external_input"]["path"], root, parameters,
            model_path=proposal["model_input"]["path"] if proposal["model_input"] else None)
        if prepared["source_sha256"] != proposal["source_bundle_sha256"]:
            raise ValueError("Workbench source changed during preparation")
        (root / "plan-review").mkdir(mode=0o700)
        _copy_proof(p_root, root / "plan-review/proposal", expected_proposal_root, "proposal.json")
        _copy_proof(r_root, root / "plan-review/review", expected_review_root, "review.json")
        (root / "review-binding.json").write_bytes(binding_bytes)
        verify_workbench_review(root, prepared)
    except (Exception, KeyboardInterrupt) as error:
        if root.is_dir():
            atomic_json(root / "reviewed-preparation-failure.json", {"status": "infrastructure_failure",
                "error_type": type(error).__name__, "reason": str(error)[:2000],
                "proposal_root": expected_proposal_root, "review_root": expected_review_root,
                "analysis_executed": False})
        raise
    return {"bundle": str(root), "analysis_executed": False, "proposal_root": expected_proposal_root,
            "review_root": expected_review_root, "review_binding_sha256": prepared["review_binding_sha256"]}


def copy_workbench_review(original, destination, expected_binding):
    """Keep native bundle replay and its separately declared execution tier."""
    from devbench.study.evidence import _read, decode_json
    original, destination = _absolute(original), _absolute(destination)
    parameters = decode_json(_read(original / "parameters.json", 1_000_000))
    if parameters.get("review_binding_sha256") != expected_binding:
        raise ValueError("Original workbench review binding changed")
    binding = verify_workbench_review(original, parameters)
    (destination / "plan-review").mkdir(mode=0o700)
    _copy_proof(original / "plan-review/proposal", destination / "plan-review/proposal", binding["proposal_root"], "proposal.json")
    _copy_proof(original / "plan-review/review", destination / "plan-review/review", binding["review_root"], "review.json")
    content = _read(original / "review-binding.json", 1_000_000)
    if hashlib.sha256(content).hexdigest() != expected_binding:
        raise ValueError("Workbench binding changed during replay preparation")
    (destination / "review-binding.json").write_bytes(content)
    verify_workbench_review(destination, decode_json(_read(destination / "parameters.json", 1_000_000)))


def require_reviewed_session(root, parameters):
    """Gate reviewed stage entry on its active trusted supervisory session.

    The outer watchdog enforces the real process-tree deadline. This marker
    prevents ordinary entry points from accidentally bypassing that wrapper;
    it is not protection from a host owner forging process state.
    """
    from devbench.study.evidence import _read, decode_json
    try:
        record = decode_json(_read(Path(root) / ".reviewed-execution.json", 100_000))
    except (OSError, ValueError) as error:
        raise PermissionError("Reviewed workbench requires whole-run supervision") from error
    if (record.get("session_id") != os.environ.get("DEVBENCH_REVIEWED_WORKBENCH_SESSION")
            or not record.get("session_id") or record.get("bundle") != str(Path(root).resolve())
            or record.get("review_binding_sha256") != parameters["review_binding_sha256"]):
        raise PermissionError("Reviewed workbench requires its matching supervisory session")
    deadline = record.get("deadline_monotonic")
    if type(deadline) not in (int, float) or not math.isfinite(deadline) or deadline <= time.monotonic():
        raise TimeoutError("Reviewed workbench supervisory deadline expired")
    return deadline-time.monotonic()


def _execute_worker(root, session_id):
    from .resources import configure_threads
    configure_threads()
    os.environ["DEVBENCH_REVIEWED_WORKBENCH_SESSION"] = session_id
    from .workbench import execute_bundle, _frozen_configuration
    parameters, _ = _frozen_configuration(Path(root))
    if _source_identity() != parameters["source_sha256"]:
        raise ValueError("Run the workbench with its reviewed retained coordinator/source")
    return execute_bundle(Path(root))


def execute_reviewed_workbench(root, output, *, allow_data_execution=False):
    if allow_data_execution is not True:
        raise PermissionError("Reviewed workbench execution requires explicit data-execution authorization")
    from .workbench import _frozen_configuration, _source_binding
    from .runtime import verify_data_identity
    from devharness_core.watchdog import run_sandboxed, HarnessResourceInterrupt, WorkerCancelled
    root = _absolute(root)
    parameters, _ = _frozen_configuration(root)
    binding = verify_workbench_review(root, parameters)
    if _source_identity() != parameters["source_sha256"]:
        raise ValueError("Run the workbench with its reviewed retained coordinator/source")
    if (root / "receipts.json").exists():
        raise ValueError("Use native replay for a sealed reviewed workbench")
    source = _source_binding(root)
    output = require_disjoint_output(output, [root, source])
    from devbench.study.permissions import require_protected_storage
    require_protected_storage(output)
    verify_data_identity(source, parameters["data_sha256"])
    output.mkdir(parents=True, exist_ok=False)
    session_id = uuid.uuid4().hex
    session_path = root / ".reviewed-execution.json"
    session = {"session_id": session_id, "bundle": str(root),
        "review_binding_sha256": parameters["review_binding_sha256"],
        "deadline_monotonic": time.monotonic()+binding["run_timeout_seconds"],
        "scope": "active_supervised_attempt_not_persistent_or_transferable_compute_permission"}
    diagnostics, result = {}, {"status": "infrastructure_failure", "bundle_root": None, "archive": None,
        "model_sessions_attempted": 0, "tokens": None, "cost_usd": None, "review_binding": binding,
        "execution_tier": parameters["execution_tier"], "failure_origin": None,
        "scope": "research_workbench_not_clinical_validation"}
    atomic_json(output / "attempt.json", {"started_at_utc": datetime.now(timezone.utc).isoformat(),
        "coordinator_pid": os.getpid(), "bundle": str(root), "run_timeout_seconds": binding["run_timeout_seconds"],
        "memory_limit_MiB": parameters["memory_mb"], "status": "starting", "model_sessions_attempted": 0})
    created_session = False
    try:
        # A marker alone is not evidence of a live process. Preserve it and
        # record the refusal instead of silently replacing an earlier attempt.
        with session_path.open("x") as handle:
            json.dump(session, handle, allow_nan=False)
        created_session = True
        execution = run_sandboxed(_execute_worker, (str(root), session_id), max_mem_mb=parameters["memory_mb"],
            timeout_sec=max(.001, session["deadline_monotonic"]-time.monotonic()), growth_window_seconds=parameters.get("growth_window_seconds", 0),
            diagnostics=diagnostics)
        result.update(status="completed_review_passed" if execution["review_passed"] else "completed_review_failed",
                      bundle_root=execution["manifest_sha256"], archive=execution["archive"], native_result=execution,
                      failure_origin=None if execution["review_passed"] else "native_review")
    except (Exception, KeyboardInterrupt) as error:
        infrastructure = isinstance(error, (HarnessResourceInterrupt, OSError, KeyboardInterrupt))
        cancelled = isinstance(error, WorkerCancelled)
        result.update(status="cancelled" if cancelled else "infrastructure_failure" if infrastructure else "execution_failure_unclassified",
                      failure_origin="operator_cancellation" if cancelled else "infrastructure" if infrastructure else "unknown",
                      error_type=type(error).__name__, reason=str(error)[:2000])
    finally:
        if created_session:
            session_path.unlink(missing_ok=True)
    atomic_json(output / "result.json", result)
    atomic_json(output / "supervision.json", diagnostics)
    from devharness_core.provenance import ExecutionDAG
    audit = ExecutionDAG(output / "audit", actor_id="reviewed-workbench-coordinator")
    audit.add_artifact(output / "attempt.json")
    audit.add_artifact(output / "result.json")
    audit.add_artifact(output / "supervision.json")
    audit.append("workbench.execution", {"status": result["status"], "bundle_root": result["bundle_root"],
        "proposal_root": binding["proposal_root"], "review_root": binding["review_root"]})
    result["receipt"] = audit.finalize()
    atomic_json(output / "root-receipt.json", result["receipt"])
    return result
