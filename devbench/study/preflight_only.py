"""Unscored orchestration through existing worker setup, with separate evidence."""
from dataclasses import asdict, is_dataclass
import hashlib
from pathlib import Path
import math
import shutil
import tempfile

from .contracts import CONDITIONS, canonical_json_bytes
from .evidence import _read, decode_json


def _value(slot, name):
    return slot[name] if isinstance(slot, dict) else getattr(slot, name)


def _slot_json(slot):
    if isinstance(slot, dict):
        return dict(slot)
    return asdict(slot) if is_dataclass(slot) else slot.model_dump(mode="json")


def select_probe_attempts(attempts):
    if not attempts:
        raise ValueError("A frozen task is required for permission probes")
    first = _slot_json(attempts[0])
    identity_field = "task_id" if "task_id" in first else "task_identity"
    selected = {}
    for slot in attempts:
        values = _slot_json(slot)
        if values[identity_field] == first[identity_field]:
            selected.setdefault(values["condition"], slot)
    if set(selected) != set(CONDITIONS):
        raise ValueError("The first task must supply all three probe conditions")
    return [selected[condition] for condition in CONDITIONS]


def require_provider_budget(sessions, maximum):
    if type(maximum) is not int or maximum < 1 or type(sessions) is not int or sessions < 0:
        raise ValueError("max_provider_sessions must be a positive integer")
    if sessions > maximum:
        raise ValueError("Schedule exceeds max_provider_sessions; no provider session was launched")


def _write(path, value):
    from .supervision import write_coordinator_json
    write_coordinator_json(path, value, replace=path.exists(), allow_data_execution=True)


def finish_probe_worker(output, condition, details, *, interrupted=False, broker=None, adapter=None,
                        runtime_sha256=None):
    """Finalize probe evidence without constructing a scored attempt record."""
    output = Path(output)
    details = {**details, "record_format": "devbench.preflight-worker/1", "preflight_only": True}
    if "attempt_id" in details:
        details["source_schedule_id"] = details.pop("attempt_id")
    probe = details.get("exec_preflight")
    analysis_started = broker is not None and broker.started is not None
    scored = bool(adapter is not None and adapter.metadata.get("inference_dispatch_attempted") is True)
    scored = scored or (output / "dispatch_checkpoint.json").exists()
    errors = list(details.get("errors", []))
    if analysis_started or scored or any(key in details for key in ("score", "grade", "workflow")):
        errors.append("probe_only_reached_scored_or_analysis_state")
    result = {"record_format": "devbench.preflight-result/1", "preflight_only": True,
        "condition": condition, "verified": bool(probe and probe.get("verified") is True and not errors and not interrupted),
        "scored_attempts": int(scored), "analysis_started": analysis_started,
        "exec_preflight": probe, "errors": errors, "interrupted": interrupted,
        "model_identity_verified": bool(probe and probe.get("model_identity_verified") is True),
        "actual_model": probe.get("actual_model") if probe else None,
        "shared_runtime_sha256": runtime_sha256}
    result["status"] = "interrupted" if interrupted else ("verified" if result["verified"] else "failed")
    details["errors"] = errors
    _write(output / "execution.json", details)
    _write(output / "probe_result.json", result)
    return result


def preflight_accounting(evidence_directories):
    """Aggregate preserved probe usage; incomplete capture remains unknown."""
    attempted, unknown, invalid, tokens, costs, durations = 0, 0, 0, [], [], []
    for directory in evidence_directories:
        directory = Path(directory)
        path = directory / "evidence.json"
        if not directory.exists():
            continue
        if not path.is_file():
            checkpoint = directory / "dispatch_checkpoint.json"
            try:
                observed = decode_json(_read(checkpoint, 100_000))
                if observed.get("scored") is not False:
                    raise ValueError("Unexpected preflight dispatch checkpoint")
                attempted += 1
            except (OSError, ValueError, TypeError, AttributeError):
                unknown += 1
            tokens.append(None); costs.append(None); durations.append(None)
            continue
        try:
            evidence = decode_json(_read(path, 2_000_000))
            if not isinstance(evidence, dict) or not isinstance(evidence.get("billing", {}), dict):
                raise ValueError("Malformed preflight evidence")
        except (OSError, ValueError, TypeError):
            invalid += 1
            unknown += 1
            tokens.append(None); costs.append(None); durations.append(None)
            continue
        dispatched = evidence.get("inference_dispatch_attempted")
        attempted += int(dispatched is True)
        unknown += int(dispatched is not True and dispatched is not False)
        billing = evidence.get("billing", {})
        tokens.append(billing.get("tokens") if dispatched is not False else 0)
        costs.append(billing.get("cost_usd") if dispatched is not False else 0.0)
        durations.append(evidence.get("duration_sec"))
    def total(values):
        if not all(type(value) in (int, float) and math.isfinite(value) and value >= 0 for value in values):
            return None
        value = sum(values)
        return value if math.isfinite(value) else None
    return {"confirmed_provider_session_attempts": attempted, "unknown_dispatch_slots": unknown,
            "invalid_evidence_slots": invalid,
            "tokens": total(tokens), "cost_usd": total(costs), "duration_sec": total(durations),
            "scope": "unscored_exec_preflight_only; unknown costs are not zero"}


def execute_preflight_only(track, plan, bundles, output_dir, workspace_parent, *, codex_executable="codex",
                           max_provider_sessions=6, allow_exec_preflight=False, preflight_conditions=None):
    if allow_exec_preflight is not True:
        raise PermissionError("Probe-only execution requires explicit preflight authorization")
    if preflight_conditions is None:
        conditions = list(CONDITIONS)
    else:
        if (not isinstance(preflight_conditions, (list, tuple)) or not preflight_conditions
                or any(condition not in CONDITIONS for condition in preflight_conditions)
                or len(set(preflight_conditions)) != len(preflight_conditions)):
            raise ValueError("Select distinct supported preflight conditions")
        conditions = [condition for condition in CONDITIONS if condition in preflight_conditions]
    require_provider_budget(len(conditions), max_provider_sessions)
    from .execution import _safe_path, _source_identity, _SOURCE_PACKAGES, _REPOSITORY, _file_digest
    from .permissions import require_protected_storage
    from .supervision import supervise_coordinator
    from .cli_identity import read_codex_identity, verify_codex_identity
    if track == "public":
        from .public_study import schedule_public_attempts
        from .public_execution import validate_public_execution_configuration, _public_attempt_worker
        plan, bundles = validate_public_execution_configuration(plan, bundles)
        scheduled, worker, budget = schedule_public_attempts(plan), _public_attempt_worker, plan.budget
    elif track == "biology":
        from .biology_planning import schedule_biology_attempts
        from .biology_execution import validate_biology_execution_configuration, _biology_attempt_worker
        plan, bundles = validate_biology_execution_configuration(plan, bundles)
        scheduled, worker, budget = schedule_biology_attempts(plan), _biology_attempt_worker, plan.budget
    elif track == "classic":
        from .planning import schedule_attempts
        from .execution import validate_execution_configuration, _attempt_worker
        plan, bundles = validate_execution_configuration(plan, bundles)
        scheduled, worker, budget = schedule_attempts(plan), _attempt_worker, plan.budgets
    else:
        raise ValueError("Unknown probe-only track")
    attempts = [slot for slot in select_probe_attempts(scheduled) if _value(slot, "condition") in conditions]
    require_provider_budget(len(attempts), max_provider_sessions)
    destination = require_protected_storage(_safe_path(output_dir))
    workspace_parent = require_protected_storage(_safe_path(workspace_parent))
    if not workspace_parent.is_relative_to(_REPOSITORY / "tmp"):
        raise ValueError("Probe workspaces must remain below repository tmp")
    private = [destination, *(Path(bundle.root) for bundle in bundles.values())]
    if any(workspace_parent.is_relative_to(path) or path.is_relative_to(workspace_parent) for path in private):
        raise ValueError("Probe workspaces and private state must be disjoint")
    if any(destination.is_relative_to(path) or path.is_relative_to(destination) for path in private[1:]):
        raise ValueError("Probe output and prepared data must be disjoint")
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    _write(destination / "plan.json", plan.model_dump(mode="json"))
    _write(destination / "probe_schedule.json", {"record_format": "devbench.preflight-schedule/1",
        "probes": [{"condition": _value(slot, "condition"), "scored": False,
            "source_task_identity": _slot_json(slot).get("task_id", _slot_json(slot).get("task_identity"))}
            for slot in attempts]})
    frozen = {name: _file_digest(destination / name) for name in ("plan.json", "probe_schedule.json")}
    manifest = {"record_format": "devbench.preflight-only/1", "preflight_only": True, "track": track,
        "plan_identity": plan.sha256(), "status": "started", "scored_attempts": 0,
        "planned_provider_sessions": len(attempts), "max_provider_sessions": max_provider_sessions,
        "requested_conditions": conditions,
        "selection": "first_frozen_task_once_per_condition", "errors": [],
        "probes": {condition: {"condition": condition, "status": "not_run", "verified": False} for condition in conditions}}
    def save():
        manifest["scored_attempts"] = sum(row.get("scored_attempts", 0) for row in manifest["probes"].values())
        manifest["model_identity_by_condition"] = {
            condition: {"verified": row.get("result", {}).get("model_identity_verified") is True,
                        "actual_model": row.get("result", {}).get("actual_model")}
            for condition, row in manifest["probes"].items()}
        manifest["all_probe_model_identities_verified"] = all(
            value["verified"] for value in manifest["model_identity_by_condition"].values())
        manifest["preflight_accounting"] = preflight_accounting(
            destination / "probes" / condition / "exec-preflight" for condition in conditions)
        _write(destination / "preflight.json", manifest)
    save()
    try:
        if budget.token_budget is not None:
            raise ValueError("The registered CLI cannot enforce a token ceiling")
        located = shutil.which(codex_executable)
        if located is None:
            raise RuntimeError("Codex executable unavailable; no probe was launched")
        executable = Path(located).resolve()
        identity, source_hash = read_codex_identity(executable), _source_identity()
        manifest.update(codex_identity=identity, source_sha256=source_hash)
        workspace_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        workspaces = [Path(tempfile.mkdtemp(prefix="pf-", dir=workspace_parent)).resolve()
                      for _ in range(max(2, len(attempts)))]
        if any(len(str(path / "control/tools.sock").encode()) > 100 for path in workspaces):
            raise ValueError("Probe workspace path exceeds Unix-socket length budget")
        runtime = None
        for slot, workspace in zip(attempts, workspaces):
            condition = _value(slot, "condition")
            output = destination / "probes" / condition
            output.mkdir(mode=0o700, parents=True)
            verify_codex_identity(identity)
            if _source_identity() != source_hash or any(_file_digest(destination / name) != value for name, value in frozen.items()):
                raise ValueError("Frozen probe code or configuration changed")
            siblings = [path for path in workspaces if path != workspace]
            denied = (*private, *(_REPOSITORY / name for name in _SOURCE_PACKAGES), *siblings)
            if track == "public":
                bundle = bundles[_value(slot, "dataset_id")]
                arguments = (plan, slot, None, bundle, workspace, output, denied, source_hash, runtime, executable)
                worker_args = (arguments, True)
            elif track == "biology":
                bundle = bundles[_value(slot, "cohort_id")]
                recipe = next(recipe for recipe in plan.recipes if recipe.recipe_id == _value(slot, "recipe_id"))
                arguments = (plan, slot, None, recipe, bundle, None, workspace, output, denied, source_hash, runtime, executable)
                worker_args = (arguments, True)
            else:
                bundle = bundles[_value(slot, "cohort_id")]
                arguments = (plan, slot, None, bundle, workspace, output, siblings, denied,
                             source_hash, [] if runtime is None else [runtime], executable)
                worker_args = (arguments, None, None, True)
            supervision = supervise_coordinator(worker, worker_args, output, budget,
                result_name="probe_result.json", allow_data_execution=True)
            row = {"condition": condition, "status": "failed", "verified": False,
                   "scored_attempts": 0, "errors": [], "supervision": supervision.as_dict()}
            try:
                receipt = next(item for item in supervision.artifacts if item.kind == "result")
                raw = _read(output / "probe_result.json", 4_000_000)
                if receipt.path != output / "probe_result.json" or hashlib.sha256(raw).hexdigest() != receipt.sha256:
                    raise ValueError("Probe result differs from supervised receipt")
                result = decode_json(raw)
                observed_scored = result.get("scored_attempts")
                if type(observed_scored) is int and observed_scored >= 0:
                    row["scored_attempts"] = observed_scored
                if (result.get("record_format") != "devbench.preflight-result/1" or result.get("condition") != condition
                        or result.get("preflight_only") is not True or type(observed_scored) is not int or observed_scored != 0
                        or result.get("analysis_started") is not False):
                    raise ValueError("Probe-only worker reached an invalid state")
                verify_codex_identity(identity)
                if _source_identity() != source_hash or any(_file_digest(destination / name) != value for name, value in frozen.items()):
                    raise ValueError("Frozen probe code or configuration changed during execution")
                row.update(result=result, verified=result["verified"] is True and supervision.failure_kind is None,
                           status=result["status"], result_sha256=receipt.sha256)
                if supervision.failure_kind is not None:
                    row["status"] = "failed"
                observed = result.get("shared_runtime_sha256")
                if result["verified"] is True and observed is None:
                    raise ValueError("Verified probe lacks its shared runtime identity")
                if observed is not None:
                    if runtime is not None and observed != runtime:
                        raise ValueError("Probe conditions have different shared runtimes")
                    runtime = observed
            except Exception as error:
                row.update(verified=False, status="failed")
                row["errors"].append(f"{type(error).__name__}: {error}"[:1000])
            manifest["probes"][condition] = row
            save()
            if supervision.interrupted or row.get("result", {}).get("interrupted"):
                raise KeyboardInterrupt
        accounting = manifest["preflight_accounting"]
        complete = (all(row["verified"] for row in manifest["probes"].values())
                    and accounting["unknown_dispatch_slots"] == 0
                    and accounting["invalid_evidence_slots"] == 0
                    and accounting["confirmed_provider_session_attempts"] == len(attempts))
        manifest["status"] = "verified" if complete else "failed"
    except KeyboardInterrupt:
        manifest["status"] = "interrupted"
        manifest["errors"].append("Remaining probe conditions retained as not_run")
    except Exception as error:
        manifest["status"] = "blocked"
        manifest["errors"].append(f"{type(error).__name__}: {error}"[:1000])
    finally:
        save()
    return {**manifest, "manifest_path": str(destination / "preflight.json"),
            "manifest_sha256": _file_digest(destination / "preflight.json")}
