"""Read-only semantic verification of retained native controlled attempts.

This verifier never launches Docker or refits a model. An external root and
configured signer keys are required; neither establishes human identity, host
attestation, qualified retention or clinical assay validity.
"""
from __future__ import annotations

import csv
import math
from pathlib import Path
import re

from devharness_core.provenance import verify_execution
from .native_contracts import NativeEffectPlan, is_governed_plan, is_reference_plan, parse_native_plan_bytes
from .native_executor import INPUTS, IDENTITIES, check_input_tables, native_request
from .native_runtime import (NATIVE_IMAGE_ID, SUPPORTED_PROFILES, LOG_LIMIT, RESULT_LIMIT, check_container_record,
                             local_endpoint, CLIENT_CONFIG)
from .trust import TrustedSigner, verify_plan_approvals
from .verification import (_require, _read, _json, _digest, _time, _finite, _Evidence,
                            _check_approvals, _check_environment)

STAGES = ("attempt_started", "plan_approvals_verified", "inputs_preserved",
          "native_execution_started", "native_execution_returned")
GOVERNED_STAGES = ("attempt_started", "plan_approvals_verified", "authority_snapshot_preserved",
                  "authority_policy_verified", "inputs_preserved", "native_execution_started", "native_execution_returned")


def _identity(plan, evidence):
    identity = _check_environment(plan, evidence)
    for name in IDENTITIES:
        _require(identity[name] == getattr(plan, name), "Native identity differs from plan: " + name)
    _require(plan.runtime_image_id == NATIVE_IMAGE_ID and plan.platform == "linux/amd64", "Unsupported native image/platform")
    environment = identity["environment"]
    _require(environment["profile_version"] in SUPPORTED_PROFILES, "Unsupported native isolation policy")
    if environment["profile_version"] == "native_container_profile/2":
        _require(type(environment["docker"].get("daemon_id")) is str
                 and re.fullmatch(r"[A-Za-z0-9:._-]{1,256}", environment["docker"]["daemon_id"]),
                 "Missing recorded Docker daemon identity")
    _require(environment["docker"]["image"] == {"id": NATIVE_IMAGE_ID, "os": "linux", "architecture": "amd64"},
             "Native image identity mismatch")
    local_endpoint(environment["docker"]["endpoint"])
    _require(type(environment["uid"]) is int and environment["uid"] > 0
             and type(environment["gid"]) is int and environment["gid"] >= 0, "Invalid recorded container user")
    required = {"devharness_controlled/native_runtime.py", "devharness_controlled/native_executor.py",
                "devharness_controlled/native_contracts.py", "devharness_controlled/native_results.py",
                "devharness_controlled/native_verification.py"}
    if is_reference_plan(plan):
        required.add("devharness_controlled/normalization_reference.py")
    _require(required <= set(identity["source"]), "Incomplete native source identity")
    _require(identity["clinical_release_allowed"] is False and identity["qualification_status"] == "candidate_unqualified",
             "Unsupported native qualification claim")
    return identity


def _same_design(observed, expected):
    for name, value in expected.items():
        if name in {"normalization_from_counts", "effect_contrast"}:
            continue
        if name in {"full_design", "reduced_design"}:
            design = observed[name]
            for key in ("columns", "rank", "residual_df"):
                _require(design[key] == value[key], "Native design differs from preserved matrix")
            _require(_finite(design["condition_number"]) and math.isclose(design["condition_number"], value["condition_number"],
                     rel_tol=1e-8, abs_tol=1e-10), "Native condition number differs from preserved matrix")
        else:
            _require(observed[name] == value and (observed[name] is False if value is False else True),
                     "Native sample/panel/design validation differs from preserved inputs: " + name)


def _same_normalization(observed, expected):
    _require(set(observed) == set(expected), "Native normalization fields differ from preserved counts")
    for key, value in expected.items():
        if type(value) is dict:
            _require(type(observed[key]) is dict and set(observed[key]) == set(value), "Normalization sample coverage differs")
            for name, factor in value.items():
                _require(_finite(observed[key][name]) and math.isclose(observed[key][name], factor, rel_tol=1e-10, abs_tol=1e-12),
                         "Native normalization factor differs from preserved counts")
        else:
            _require(observed[key] == value, "Native normalization reference differs from preserved counts")


def _inputs(plan, evidence, event, workspace):
    supplied = event["payload"]["inputs"]
    _require(type(supplied) is dict and set(supplied) == set(INPUTS), "Incomplete native preserved input set")
    for name, (field, limit) in INPUTS.items():
        ref = evidence.reference(name)
        _require(ref["kind"] == "file" and ref == supplied[name] and ref["sha256"] == getattr(plan, field)
                 and ref["size_bytes"] <= limit, "Preserved native input differs from plan: " + name)
    request_ref = evidence.reference("request.json")
    _require(event["payload"]["request"] == request_ref and evidence.json("request.json") == native_request(plan),
             "Native request differs from signed plan")
    validation = check_input_tables(plan, lambda name: evidence.bytes(name, INPUTS[name][1]))
    _require(event["payload"]["staging_dir"] == str(Path(workspace)/"inputs"), "Native inputs use another attempt workspace")
    _same_design(event["payload"]["input_validation"], validation)
    _same_normalization(event["payload"]["input_validation"]["normalization_from_counts"], validation["normalization_from_counts"])
    if isinstance(plan, NativeEffectPlan):
        _require(event["payload"]["input_validation"]["effect_contrast"] == validation["effect_contrast"],
                 "Preserved effect specification changed")
    return validation


def _transport(plan, evidence, event, completed, identity, approvals, signers, started_at, workspace, authority_context=None):
    reference = evidence.reference("native-transport.json")
    _require(event["payload"]["transport"] == reference, "Transport is not linked to returned event")
    transport = evidence.json("native-transport.json")
    _require(transport["schema_version"] == "native_transport/1" and transport["status"] in {"ok", "failed"},
             "Unsupported native transport record")
    profile = transport["profile"]
    from .executor import THREAD_VARIABLES
    environment = identity["environment"]
    expected = {"schema_version": environment["profile_version"], "image_id": plan.runtime_image_id, "platform": plan.platform,
                "user": f'{environment["uid"]}:{environment["gid"]}', "memory_bytes": plan.max_memory_mb*1024**2,
                "nano_cpus": plan.max_cpus*10**9, "pids_limit": 64, "timeout_sec": plan.timeout_sec,
                "tmpfs": "rw,nosuid,nodev,noexec,size=67108864,mode=1777", "threads": list(THREAD_VARIABLES),
                "log_limit_bytes": LOG_LIMIT, "result_limit_bytes": RESULT_LIMIT}
    _require(set(profile) == set(expected) | {"input_dir", "output_dir"}, "Unsupported native profile fields")
    _require({key: profile[key] for key in expected} == expected, "Native profile differs from signed limits")
    for name in ("input_dir", "output_dir"):
        value = profile[name]
        _require(type(value) is str and Path(value).is_absolute()
                 and not any(c == "," or ord(c) < 32 or ord(c) == 127 for c in value), "Invalid preserved mount path")
    _require(Path(profile["input_dir"]).name == "inputs" and Path(profile["output_dir"]).name == "native-output"
             and Path(profile["input_dir"]).parent == Path(profile["output_dir"]).parent == Path(workspace),
             "Unexpected native staging layout")
    _require(_finite(transport["elapsed_seconds"]) and transport["elapsed_seconds"] >= 0
             and transport["memory_peak_mb"] is None, "Unsupported native resource measurement")
    name = transport["container_name"]
    _require(type(name) is str and re.fullmatch(r"devharness-native-[0-9a-f]{32}", name), "Invalid owned container name")
    for key in ("created", "finished", "dispatch_observed"):
        if transport.get(key) is not None:
            check_container_record(transport[key], profile, plan.sha256(), name)
    from .native_runtime import create_arguments
    command = transport["command"]
    _require(type(command) is list and len(command) > 5 and Path(command[0]).is_absolute()
             and command[1:5] == ["--config", str(Path(profile["input_dir"]).parent / "native-client"),
                                  "--host", environment["docker"]["endpoint"]]
             and command[5:] == create_arguments(profile, plan.sha256(), name), "Unapproved native invocation")
    _require(evidence.json("config.json") == CLIENT_CONFIG, "Unapproved Docker client configuration")
    journal = [_json(raw) for raw in evidence.bytes("lifecycle.jsonl", LOG_LIMIT).splitlines()]
    _require(journal and journal[0]["event"] == "intent" and journal[-1]["event"] == "cleanup", "Missing durable native lifecycle intent/cleanup")
    _require(journal[0]["payload"] == {"image_id": plan.runtime_image_id, "profile": profile, "command": command},
             "Lifecycle intent differs from transport")
    previous_at = started_at
    for entry in journal:
        _require(entry["schema_version"] == "native_lifecycle/1" and entry["plan_sha256"] == plan.sha256()
                 and entry["container_name"] == name and previous_at <= _time(entry["recorded_at"]) <= _time(event["timestamp_utc"]),
                 "Inconsistent native lifecycle identity/time")
        previous_at = _time(entry["recorded_at"])
    _require(journal[-1]["payload"] == transport["cleanup"], "Lifecycle cleanup differs from transport")
    governed=is_governed_plan(plan)
    if governed:
        _require(authority_context is not None,"Governed transport lacks verified admission context")
        _verify_governed_dispatch(plan,transport,journal,authority_context,started_at,_time(event["timestamp_utc"]),approvals)
    else:
        _require("launch_authority" not in transport and "dispatch_observed" not in transport,
                 "Legacy plan unexpectedly claims governed dispatch")
    if completed or transport["status"] == "ok":
        _require(transport["status"] == "ok" and transport["created"] is not None and transport["finished"] is not None,
                 "Completed native execution lacks runtime evidence")
        _require(transport["memory_limit_verified"] is True, "Native memory ceiling was not verified")
        state = transport["finished"]["State"]
        _require(transport["created"]["Id"] == transport["finished"]["Id"] == transport["cleanup"]["container_id"],
                 "Container identity changed across execution/cleanup")
        _require(transport["client_exit_code"] == 0 and state["ExitCode"] == 0 and state["Running"] is False
                 and state["Status"] == "exited" and state["OOMKilled"] is False
                 and transport["timed_out"] is False and transport["output_limit_exceeded"] is False,
                 "Failed or resource-limited native execution claimed success")
        _require(transport["cleanup"]["attempted"] is True and transport["cleanup"]["removed"] is True,
                 "Successful native attempt lacks verified cleanup")
        _require(_time(state["FinishedAt"]) >= _time(state["StartedAt"]), "Native process times run backwards")
        receipts = transport["launch_approvals"]
        _require(type(receipts) is list and len(receipts) == 2, "Missing native launch approval receipts")
        verified_at = _time(receipts[0]["verified_at"])
        _require(started_at <= verified_at <= _time(event["timestamp_utc"]), "Native launch authorization time is outside attempt")
        expected_receipts = verify_plan_approvals(plan, approvals, signers, now=verified_at)
        _require(receipts == expected_receipts, "Native launch approval receipt mismatch")
        _require(verified_at <= _time(state["StartedAt"]) <= _time(state["FinishedAt"]) <= _time(event["timestamp_utc"]),
                 "Native process ran outside its launch authorization/return interval")
        stages = ["intent", "created", "start_authorized", "attach_started", "finished", "cleanup"]
        if profile["schema_version"] == "native_container_profile/2":
            stages.insert(3, "start_requested")
        if governed:
            stages.insert(stages.index("finished"),"authority_dispatch_observed")
        _require([entry["event"] for entry in journal] == stages,
                 "Completed native attempt has an incomplete lifecycle journal")
        entries = {entry["event"]: entry for entry in journal}
        expected_authorization={"container_id":transport["created"]["Id"],"receipts":receipts}
        if governed:expected_authorization["authority"]=transport["launch_authority"]
        _require(journal[1]["payload"] == {"container_id": transport["created"]["Id"]}
                 and journal[2]["payload"] == expected_authorization
                 and entries["attach_started"]["payload"]["container_id"] == transport["created"]["Id"]
                 and entries["finished"]["payload"] == {"container_id": transport["finished"]["Id"], "state": state},
                 "Native lifecycle events do not bind the actual transport")
        if profile["schema_version"] == "native_container_profile/2":
            _require(entries["start_requested"]["payload"] == {"container_id": transport["created"]["Id"]}
                     and _time(entries["start_requested"]["recorded_at"]) <= _time(state["StartedAt"]),
                     "Native process start predates durable start intent")
        for log in ("stdout.log", "stderr.log"):
            _require(evidence.reference(log)["size_bytes"] <= LOG_LIMIT, "Native log exceeds retention bound")
    else:
        _require(isinstance(transport.get("error"), str) and transport["error"], "Failed transport lacks error evidence")
    return transport


def _verify_governed_dispatch(plan, transport, journal, context, before, after, approvals):
    from .authority_store import authorize_snapshot
    events={entry["event"]:entry for entry in journal}
    _require(len(events)==len(journal),"Repeated governed lifecycle event")
    decision=transport.get("launch_authority")
    if decision is None:
        _require(transport["status"]!="ok" and transport.get("dispatch_observed") is None
                 and transport.get("launch_approvals") is None
                 and not {"start_authorized","start_requested","attach_started","authority_dispatch_observed"}&set(events),
                 "Governed dispatch occurred without authority verification")
        return
    _require(type(decision) is dict,"Malformed governed launch authority")
    at=_time(decision["checked_at"])
    _require(before<=at<=after,"Governed dispatch decision lies outside the native interval")
    expected=authorize_snapshot(plan,context["signers"],context["administrators"],context["snapshot"],
                                now=at,expected_checkpoint=context["checkpoint"])
    _require(decision==expected,"Governed launch decision does not bind current preserved policy/grants")
    receipts=transport.get("launch_approvals")
    _require(receipts==verify_plan_approvals(plan,approvals,context["signers"],now=at),
             "Governed launch approval receipt mismatch")
    if "start_authorized" in events:
        entry=events["start_authorized"]
        _require(entry["payload"]=={"container_id":transport["created"]["Id"],"authority":decision,"receipts":receipts}
                 and at<=_time(entry["recorded_at"]),
                 "Start authorization omits or predates its authority decision")
    def check_start(stamp):
        _require(at<=stamp<=after,"Governed process start lies outside its authorization interval")
        authorize_snapshot(plan,context["signers"],context["administrators"],context["snapshot"],
                           now=stamp,expected_checkpoint=context["checkpoint"])
        verify_plan_approvals(plan,approvals,context["signers"],now=stamp)
    finished_start=(transport.get("finished") or {}).get("State",{}).get("StartedAt")
    if isinstance(finished_start,str) and not finished_start.startswith("0001-"):
        check_start(_time(finished_start))
    observed=transport.get("dispatch_observed")
    if observed is None:
        _require(transport["status"]!="ok" and "authority_dispatch_observed" not in events,
                 "Governed transport lacks physical daemon-start observation")
        return
    _require("authority_dispatch_observed" in events and events["authority_dispatch_observed"]["payload"]=={"container":observed},
             "Governed daemon observation is not bound to its journal")
    _require(observed["Id"]==transport["created"]["Id"],"Governed observed container identity differs")
    state=observed["State"]
    _require(state["Status"] in {"running","exited"} and state["Running"] is (state["Status"]=="running"),
             "Invalid governed daemon-start state")
    observed_at=_time(events["authority_dispatch_observed"]["recorded_at"])
    _require(at<=_time(state["StartedAt"])<=observed_at<=after,
             "Governed daemon start lies outside its authority/observation interval")
    check_start(_time(state["StartedAt"]))
    _require("attach_started" in events and _time(events["attach_started"]["recorded_at"])<=observed_at,
             "Governed observation predates client dispatch")
    if transport.get("finished") is not None:
        _require(transport["finished"]["State"]["StartedAt"]==state["StartedAt"],"Governed native process start changed")


def verify_native_execution(manifest_path, expected_root_hash, trusted_signers, *, authority_administrators=None):
    from .native_results import validate_native_result

    report = {"schema_version": "controlled_native_evidence_verification/1", "valid": False,
              "integrity_valid": False, "controlled_evidence_valid": False, "errors": [],
              "execution_status": None, "analysis_completed": False, "approval_signatures_verified": 0,
              "clinical_release_allowed": False, "compliance_certified": False,
              "qualification_status": "candidate_unqualified",
              "authorization_scope": "configured keys at recorded times; not a new execution authorization",
              "identity_scope": "human identity, trusted time, host attestation and qualified retention not established"}
    try:
        _require(type(expected_root_hash) is str and re.fullmatch(r"[0-9a-f]{64}", expected_root_hash), "Explicit expected root required")
        path = Path(manifest_path)
        _require(path.is_absolute() and not path.is_symlink(), "Manifest must be absolute and not a symlink")
        integrity = verify_execution(path, expected_root_hash)
        report.update(integrity_valid=integrity["valid"], root_hash=integrity["root_hash"])
        _require(integrity["valid"], "Integrity verification failed: " + "; ".join(integrity["errors"]))
        _require(isinstance(trusted_signers, (list, tuple, dict)) and trusted_signers, "Explicit signer trust required")
        for signer in trusted_signers.values() if isinstance(trusted_signers, dict) else trusted_signers:
            TrustedSigner.model_validate(signer)
        manifest = _json(_read(path))
        _require(_digest({k:v for k,v in manifest.items() if k != "root_hash"}) == expected_root_hash, "Manifest changed during verification")
        nodes = []
        for node_id in manifest["node_ids"]:
            node = _json(_read(path.parent / "nodes" / (node_id + ".json")))
            _require(_digest({k:v for k,v in node.items() if k != "node_id"}) == node_id, "Native node changed during verification")
            nodes.append(node)
        _require(len(nodes) >= 2 and nodes[-1]["kind"] == "attempt_finished", "Incomplete native controlled attempt")
        evidence = _Evidence(path.parent, manifest["artifacts"])
        plan = parse_native_plan_bytes(evidence.bytes("plan.json", 1024*1024))
        outcome = evidence.json("outcome.json", 1024*1024)
        _require(outcome == nodes[-1]["payload"] and outcome["schema_version"] == "controlled_native_execution/1"
                 and outcome["clinical_release_allowed"] is False and outcome["qualification_status"] == "candidate_unqualified",
                 "Unsupported or inconsistent native outcome")
        status = outcome["status"]
        _require(status in {"analysis_complete_pending_review", "failed"}, "Unsupported native outcome status")
        completed = status == "analysis_complete_pending_review"
        expected_stages=GOVERNED_STAGES if is_governed_plan(plan) else STAGES
        stages = list(expected_stages if completed else expected_stages[:len(nodes)-1]) + ["attempt_finished"]
        _require([node["kind"] for node in nodes] == stages, "Missing, duplicated or reordered native stages")
        at = _time(manifest["created_at_utc"])
        for index, node in enumerate(nodes):
            _require(node["parents"] == ([nodes[index-1]["node_id"]] if index else []), "Broken native causal sequence")
            _require(_time(node["timestamp_utc"]) >= at, "Native event times run backwards")
            at = _time(node["timestamp_utc"])
        _require(_time(manifest["finalized_at_utc"]) >= at, "Manifest finalized before native events")
        workspace = nodes[0]["payload"]["workspace"]
        _require(type(workspace) is str and Path(workspace).is_absolute() and ".." not in Path(workspace).parts,
                 "Invalid native attempt workspace")
        _require(manifest["analysis_fingerprint"] == outcome["plan_sha256"] == plan.sha256()
                 and nodes[0]["payload"] == {"plan_sha256": plan.sha256(), "workspace": workspace}, "Native plan is not consistently bound")
        _require(_finite(outcome["elapsed_seconds"]) and outcome["elapsed_seconds"] >= 0
                 and outcome["memory_peak_mb"] is None, "Unsupported native outcome measurements")
        by_kind = {node["kind"]: node for node in nodes}
        authority_context=None
        if "plan_approvals_verified" in by_kind:
            approvals = _check_approvals(plan, evidence, by_kind["plan_approvals_verified"], _time(nodes[0]["timestamp_utc"]), trusted_signers)
            report["approval_signatures_verified"] = 2
        if "authority_snapshot_preserved" in by_kind:
            from .authority_store import MAX_SNAPSHOT_BYTES
            preserved=by_kind["authority_snapshot_preserved"]
            names={"authority-snapshot.json","authority-administrators.json","authority-checkpoint.json"}
            payload=preserved["payload"]
            _require(type(payload) is dict and set(payload)=={"references","store_path"} and set(payload["references"])==names,
                     "Missing governed policy snapshot references")
            for name in names:
                _require(payload["references"][name]==evidence.reference(name),"Authority snapshot reference differs from preserved object")
            path_value=payload["store_path"]
            _require(type(path_value) is str and Path(path_value).is_absolute() and ".." not in Path(path_value).parts
                     and not Path(path_value).is_relative_to(Path(workspace)) and not Path(workspace).is_relative_to(Path(path_value)),
                     "Invalid or overlapping governed authority location")
            report["policy_at_admission_verified"]=False
            if "authority_policy_verified" in by_kind:
                from .authority_evidence import verify_admission
                verified_policy=by_kind["authority_policy_verified"]
                _require(type(verified_policy["payload"]) is dict and set(verified_policy["payload"])=={"decision"},"Malformed authority verification event")
                authority_context=verify_admission(plan,evidence.json("authority-snapshot.json",MAX_SNAPSHOT_BYTES),
                    evidence.json("authority-checkpoint.json",MAX_SNAPSHOT_BYTES),evidence.json("authority-administrators.json",65536),
                    verified_policy["payload"]["decision"],authority_administrators,trusted_signers,
                    by_kind["plan_approvals_verified"]["payload"]["receipts"],_time(preserved["timestamp_utc"]),_time(verified_policy["timestamp_utc"]))
                report.update(policy_at_admission_verified=True,
                    authority_administrative_signatures_verified=len(authority_context["snapshot"]["records"]))
        if "inputs_preserved" in by_kind:
            _require(plan.data_classification != "restricted_clinical", "Clinical native admission is not implemented")
            identity = _identity(plan, evidence)
            validation = _inputs(plan, evidence, by_kind["inputs_preserved"], workspace)
            if is_reference_plan(plan):
                report.update(normalization_reference_binding_verified=True,
                              normalization_reference_suitability_verified=False)
        if "native_execution_started" in by_kind:
            event = by_kind["native_execution_started"]
            verify_plan_approvals(plan, approvals, trusted_signers, now=_time(event["timestamp_utc"]))
            _require(event["payload"] == {"request_sha256": evidence.reference("request.json")["sha256"],
                     "runtime_image_id": plan.runtime_image_id, "generated_code": False, "model_service_calls": 0},
                     "Unapproved native start event")
        if "native_execution_returned" in by_kind:
            event = by_kind["native_execution_returned"]
            transport=_transport(plan, evidence, event, completed, identity, approvals, trusted_signers,
                       _time(by_kind["native_execution_started"]["timestamp_utc"]), workspace,authority_context)
            if is_governed_plan(plan):report["policy_at_dispatch_verified"]=transport.get("dispatch_observed") is not None
            result_ref = event["payload"]["result"]
            _require(result_ref == evidence.reference("result.json", required=False), "Native result is not linked to returned event")
            if completed:
                _require(result_ref is not None, "Completed native attempt has no result")
                result = evidence.json("result.json", RESULT_LIMIT)
                verdict = validate_native_result(plan, result)
                _require(verdict["usable"], "Native result is not usable")
                from .native_effects import primary_result, verify_preserved_effect
                primary = primary_result(plan, result)
                _same_design(primary["evidence"]["input_validation"], validation)
                _same_normalization(primary["evidence"]["normalization"], validation["normalization_from_counts"])
                if isinstance(plan, NativeEffectPlan):
                    import io
                    import numpy as np
                    rows = list(csv.reader(io.StringIO(evidence.bytes("full-design.csv", 8*1024*1024).decode("utf-8"))))
                    full = np.asarray([row[1:] for row in rows[1:]], dtype=float)
                    verify_preserved_effect(plan, result, validation["effect_contrast"], full)
                _require(outcome["qualification_limits"] == verdict["qualification_limits"], "Qualification limits were omitted or changed")
                report["qualification_limits"] = verdict["qualification_limits"]
        if not completed:
            _require(type(outcome.get("error")) is str and outcome["error"] and type(outcome.get("error_type")) is str
                     and outcome["error_type"], "Failed native attempt lacks error evidence")
        _require(verify_execution(path, expected_root_hash)["valid"], "Evidence changed during native semantic verification")
        report.update(valid=True, controlled_evidence_valid=True, execution_status=status, analysis_completed=completed)
    except (OSError, ValueError, TypeError, KeyError, IndexError, StopIteration, AttributeError, OverflowError, RecursionError, csv.Error) as error:
        report["errors"].append(str(error) or type(error).__name__)
    return report
