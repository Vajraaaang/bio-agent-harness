"""Idempotent local submission around the signed native numerical executor.

Request IDs are consumed once. Repeating an existing request observes its state;
it never starts another analysis, including after an interrupted dispatch. Actor
labels are local operator assertions, not independently authenticated identities.
"""
from __future__ import annotations

import os
from pathlib import Path

from devharness_core.provenance import verify_execution
from .contracts import canonical_json_bytes
from .native_contracts import is_governed_plan, parse_native_plan_bytes
from .native_executor import execute_native_plan
from .native_verification import verify_native_execution
from .run_registry import RunRegistry, _path, _text
from .trust import Approval, TrustedSigner
from .verification import _Evidence, _digest, _json, _read, _require

PROGRESS_EVENTS = {"intent": "runtime_intent", "created": "container_observed",
                   "start_authorized": "launch_authorized", "start_requested": "start_requested"}


def _entries(values):
    if isinstance(values, dict):
        values = list(values.values())
    _require(isinstance(values, (list, tuple)), "Approvals and configured trust must be explicit lists")
    raw = [item.model_dump(mode="json") if isinstance(item, (Approval, TrustedSigner)) else item for item in values]
    _require(len(canonical_json_bytes(raw)) <= 1024*1024, "Approval/trust request exceeds bound")
    return raw


def operation_binding(attempt, registry_id):
    return {"schema_version": "operated_native_binding/1", "registry_id": registry_id,
            "attempt_id": attempt["attempt_id"], "plan_sha256": attempt["plan_sha256"],
            "submission_sha256": attempt["submission_sha256"]}


def verify_saved_native_attempt(attempt, registry_id, trusted_signers, *, authority_administrators=None):
    """Check the actual saved receipt/CAS against registered identity, without fitting."""
    workspace = _path(attempt["workspace"])
    receipt = _json(_read(workspace/"receipt.json", limit=1024*1024))
    manifest_path = workspace/"audit/manifest.json"
    _require(receipt["manifest_path"] == str(manifest_path), "Saved native receipt names another workspace")
    verified = verify_native_execution(manifest_path, receipt["root_hash"], trusted_signers,
        **({"authority_administrators":authority_administrators} if authority_administrators is not None else {}))
    _require(verified["valid"], "Saved native semantics are invalid: " + "; ".join(verified["errors"]))
    manifest = _json(_read(manifest_path))
    _require(_digest({key: value for key, value in manifest.items() if key != "root_hash"}) == receipt["root_hash"],
             "Native manifest changed during operated verification")
    evidence = _Evidence(manifest_path.parent, manifest["artifacts"])
    plan = parse_native_plan_bytes(evidence.bytes("plan.json", 1024*1024))
    _require(plan.sha256() == attempt["plan_sha256"], "Saved native plan differs from registration")
    _require(evidence.json("operation.json") == operation_binding(attempt, registry_id), "Native operation binding mismatch")
    first = _json(_read(manifest_path.parent/"nodes"/(manifest["node_ids"][0]+".json")))
    _require(first["payload"]["workspace"] == str(workspace), "Saved native history belongs to another workspace")
    _require(verify_execution(manifest_path, receipt["root_hash"])["valid"], "Native evidence changed during registry binding checks")
    return {"root_hash": receipt["root_hash"], "status": verified["execution_status"],
            "analysis_completed": verified["analysis_completed"], "verification": verified}


def submit_native_plan(plan, approvals, trusted_signers, *, registry_dir, request_id, actor_id,
                       counts_path, metadata_path, full_design_path, reduced_design_path, output_dir,
                       authority_store_dir=None, authority_administrators=None, authority_checkpoint=None):
    """Register, lease and execute once. Existing requests return current state only."""
    plan = parse_native_plan_bytes(plan.canonical_bytes())
    actor_id = _text(actor_id, "actor_id")
    approvals, signers = _entries(approvals), _entries(trusted_signers)
    workspace, registry_path = _path(output_dir), _path(registry_dir)
    _require(not workspace.is_relative_to(registry_path) and not registry_path.is_relative_to(workspace),
             "Registry and native workspace must not overlap")
    inputs = {"counts_path": str(_path(counts_path)), "metadata_path": str(_path(metadata_path)),
              "full_design_path": str(_path(full_design_path)), "reduced_design_path": str(_path(reduced_design_path))}
    _require(all(not Path(path).is_relative_to(workspace) and not Path(path).is_relative_to(registry_path)
                 for path in inputs.values()), "Submission inputs must be outside new workspace and registry")
    specification = {"schema_version": "operated_native_submission_spec/1", "plan_sha256": plan.sha256(),
                     "approvals_sha256": _digest(approvals), "trust_registry_sha256": _digest(signers),
                     "inputs": inputs, "workspace": str(workspace)}
    authority_arguments={}
    if is_governed_plan(plan):
        from .authority_evidence import administrator_values
        authority_path=_path(authority_store_dir) if authority_store_dir is not None else None
        if authority_path is not None:
            _require(not authority_path.is_relative_to(registry_path) and not registry_path.is_relative_to(authority_path),
                     "Authority and operation registries must not overlap")
        administrators=administrator_values(authority_administrators) if authority_administrators is not None else None
        specification["authority"]={"store_path":str(authority_path) if authority_path else None,
            "administrators_sha256":_digest(administrators),"checkpoint_sha256":_digest(authority_checkpoint)}
        authority_arguments={"authority_store_dir":authority_path,"authority_administrators":administrators,
                             "authority_checkpoint":authority_checkpoint}
    else:
        _require(all(value is None for value in (authority_store_dir,authority_administrators,authority_checkpoint)),
                 "Authority configuration requires a governed plan")
    registry = RunRegistry(registry_path)
    attempt = registry.register(request_id, plan.sha256(), str(workspace), actor_id, submission_sha256=_digest(specification))
    result = {"schema_version": "operated_native_submission/1", "registry_id": registry.registry_id,
              "attempt_id": attempt["attempt_id"], "started_new_execution": False,
              "clinical_release_allowed": False, "identity_scope": "caller_asserted_operator; configured-key analysis approvals"}
    if not attempt["created"]:
        return {**result, "attempt": attempt, "status": "existing_request", "registry_checkpoint": registry.export_checkpoint()}
    with registry.lease(attempt["attempt_id"]):
        registry.append_event(attempt["attempt_id"], "started", {"submission": specification,
                              "coordinator_pid": os.getpid(), "pid_scope": "diagnostic only; never authority to signal"}, actor_id)
        result["started_new_execution"] = True
        binding = operation_binding(attempt, registry.registry_id)

        def record_progress(entry):
            kind = PROGRESS_EVENTS.get(entry["event"])
            if kind is None:
                return
            _require(entry["plan_sha256"] == plan.sha256()
                     and entry["container_name"] == "devharness-native-"+attempt["attempt_id"], "Native dispatch identity changed")
            registry.append_event(attempt["attempt_id"], kind, entry, actor_id)

        try:
            numerical = execute_native_plan(plan, approvals, signers, **inputs, output_dir=workspace,
                                            operation=binding, on_checkpoint=record_progress,**authority_arguments)
        except Exception as error:
            # A process kill never reaches this block: its lease is released by
            # the OS and the nonterminal registry record remains for recovery.
            failure = {"error_type": type(error).__name__, "error": str(error)[:6000],
                       "analysis_completed": False, "clinical_release_allowed": False}
            try:
                registry.append_event(attempt["attempt_id"], "failed", failure, actor_id)
                result.update(status="failed", **failure)
            except Exception as registry_error:
                result.update(status="requires_reconciliation", error=str(registry_error)[:6000],
                              execution_error=failure)
        else:
            try:
                if numerical["status"] == "analysis_complete_pending_review":
                    saved = verify_saved_native_attempt(attempt, registry.registry_id, signers,
                        **({"authority_administrators":authority_administrators} if authority_administrators is not None else {}))
                    _require(saved["analysis_completed"], "Native completion is not verified")
                    terminal, payload = "completed", {"execution_root_hash": saved["root_hash"],
                        "execution_status": saved["status"], "clinical_release_allowed": False}
                else:
                    terminal, payload = "failed", {"execution_root_hash": numerical["receipt"]["root_hash"],
                        "execution_status": numerical["status"], "error_type": numerical.get("error_type"),
                        "error": numerical.get("error"), "clinical_release_allowed": False}
                # The original receipt must exist and verify before recording
                # completion. A lost commit is reconciled; analysis is not rerun.
                registry.append_event(attempt["attempt_id"], terminal, payload, actor_id)
                result.update(status=terminal, execution=numerical)
            except Exception as error:
                result.update(status="requires_reconciliation", error_type=type(error).__name__, error=str(error)[:6000],
                              execution_receipt=numerical.get("receipt"))
        result.update(attempt=registry.get(attempt["attempt_id"]), registry_checkpoint=registry.export_checkpoint())
    return result
