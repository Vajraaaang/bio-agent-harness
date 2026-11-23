"""Capture and verify adaptive investigation lineage and dependency closure."""
from __future__ import annotations

from pathlib import Path

from ._replay_archive import (
    INVESTIGATION_REPLAY_FORMAT,
    _SHA, _audit_entitlement, _bind_replay_use, _canonical, _document,
    _inert_entitlement, _known_input_digests, _reference_roles, _reject_input_alias,
    _require_replay_scope, _restricted_replay_members, _sealed_replay, _sha,
)


def _investigation_dependencies(snapshot):
    """Derive roles from actual owner records; callers cannot label provenance."""
    bindings, external, locks = {}, [], []
    for _, subject in sorted(snapshot["publication_subjects"].items()):
        for field, digest_field, role in (("source_path", "source_sha256", "accepted_output"),
                                        ("packet_path", "packet_sha256", "original_review_packet")):
            if subject[field] is not None:
                _bind_replay_use(bindings, subject[field], subject[digest_field], role,
                                 {"owner_kind": "publication_subject", "subject_id": subject["subject_id"]})
        locks.append({"kind": "publication_subject", "sha256": subject["subject_id"],
            "profile_sha256": _sha(_canonical(subject["profile"])),
            "allocation_sha256": _sha(_canonical(subject["allocation"])),
            "accepted_output_sha256": subject["accepted_record_sha256"], "envelope_sha256": subject["envelope_sha256"]})
    for reference in snapshot.get("runtime_references", []):
        _verify_runtime_reference(snapshot, reference)
        _bind_replay_use(bindings, reference["path"], reference["sha256"], reference["role"],
            {"owner_kind": "runtime_attempt", "attempt_id": reference["attempt_id"],
             "artifact_version_id": reference["artifact_version_id"], "port": reference["port"],
             "token_sha256": reference["token_sha256"]})
    for version, record in sorted(snapshot["artifacts"].items()):
        if record["producer"] is None or record["artifact"]["kind"] == "counts":
            external.append({"role": "original_input", "version_id": version, "sha256": record["artifact"]["sha256"]})
        elif record["artifact"]["sha256"] not in {item["sha256"] for item in bindings.values()}:
            external.append({"role": "unresolved_derived_output", "version_id": version, "sha256": record["artifact"]["sha256"]})
    for attempt_id, record in sorted(snapshot["dispatches"].items()):
        envelope = record["envelope"]
        locks.append({"kind": "exact_dispatch", "attempt_id": attempt_id,
            "envelope_sha256": _sha(_canonical(envelope)), "environment_sha256": envelope["environment_sha256"],
            "parameters_sha256": envelope["step"]["parameters_sha256"],
            "profile_sha256": _sha(_canonical(envelope["step"]["profile"])), "basis": envelope["basis"]})
    for snapshot_id, reference in sorted(snapshot["literature"]["snapshots"].items()):
        _bind_replay_use(bindings, reference["path"], reference["sha256"], "frozen_literature_snapshot",
                         {"owner_kind": "literature_snapshot", "snapshot_id": snapshot_id})
    for cycle_id, cycle in sorted(snapshot["reviews"]["cycles"].items()):
        for name, value in sorted(cycle.items()):
            if name.endswith("sha256") and type(value) is str and _SHA.fullmatch(value):
                locks.append({"kind": "original_review_binding", "cycle_id": cycle_id, "field": name, "sha256": value})
    for use in _required_runtime_output_uses(snapshot):
        if use["scope"] == "external_input":
            external.append({**use, "dependency_role": "excluded_runtime_output_use"})
    if snapshot.get("scientific_material") is not None:
        locks.append({"kind": "owner_scientific_material", "sha256": _sha(_canonical(snapshot["scientific_material"]))})
    return bindings, external, locks


def _verify_runtime_reference(snapshot, reference):
    fields = {"owner_root", "attempt_id", "token_sha256", "partition", "role", "artifact_version_id", "port", "path", "sha256"}
    if set(reference) != fields or reference["partition"] != "training":
        raise ValueError("investigation_runtime_partition_changed")
    dispatch = snapshot["dispatches"][reference["attempt_id"]]
    outcome = snapshot["outcomes"][reference["attempt_id"]]
    token, envelope = dispatch["token"], dispatch["envelope"]
    root = Path(reference["owner_root"])
    if (not root.is_absolute() or ".." in root.parts or token["backend_id"] != "local_exploratory_oci_1"
            or _sha(_canonical(token)) != reference["token_sha256"] or envelope["step"]["held_out"]
            or envelope["step"]["purpose"] == "protected_evaluation"):
        raise ValueError("investigation_runtime_owner_binding_changed")
    if reference["role"] == "original_oci_receipt":
        expected_path = root / "adaptive-oci/runs" / reference["attempt_id"] / "receipt.json"
        if reference["artifact_version_id"] is not None or reference["port"] is not None or reference["sha256"] != outcome["receipt_sha256"]:
            raise ValueError("investigation_original_runtime_receipt_changed")
    else:
        if reference["role"] not in {"accepted_output", "unaccepted_output"}:
            raise ValueError("investigation_runtime_reference_role_invalid")
        outputs = outcome["outputs"] if reference["role"] == "accepted_output" else outcome.get("staged_outputs", {})
        artifact = outputs.get(reference["port"])
        if (artifact is None or artifact["version_id"] != reference["artifact_version_id"]
                or artifact["sha256"] != reference["sha256"] or artifact["kind"] == "counts"):
            raise ValueError("investigation_runtime_output_changed")
        expected_path = root / "adaptive-oci/training" / reference["sha256"]
    if reference["path"] != str(expected_path):
        raise ValueError("investigation_runtime_logical_path_changed")


def _required_runtime_output_uses(snapshot):
    """Derive output obligations from original outcomes, not a supplied ref list."""
    excluded = _known_input_digests(snapshot)
    uses = []
    for attempt_id, outcome in sorted(snapshot["outcomes"].items()):
        token = snapshot["dispatches"][attempt_id]["token"]
        if token["backend_id"] != "local_exploratory_oci_1":
            continue
        for role, outputs in (("accepted_output", outcome["outputs"]), ("unaccepted_output", outcome.get("staged_outputs", {}))):
            for port, artifact in sorted(outputs.items()):
                uses.append({"attempt_id": attempt_id, "role": role, "port": port,
                    "artifact_version_id": artifact["version_id"], "sha256": artifact["sha256"],
                    "token_sha256": _sha(_canonical(token)),
                    "scope": "external_input" if artifact["kind"] == "counts" or artifact["sha256"] in excluded else "required_payload"})
    return uses


def _runtime_output_use_closure(snapshot):
    expected = _required_runtime_output_uses(snapshot)
    key_fields = ("attempt_id", "role", "port", "artifact_version_id", "sha256", "token_sha256")
    required = {tuple(use[name] for name in key_fields) for use in expected if use["scope"] == "required_payload"}
    allowed = {tuple(use[name] for name in key_fields) for use in expected}
    actual = set()
    for reference in snapshot.get("runtime_references", []):
        if reference["role"] not in {"accepted_output", "unaccepted_output"}:
            continue
        key = tuple(reference[name] for name in key_fields)
        if key in actual:
            raise ValueError("investigation_runtime_output_use_duplicate")
        actual.add(key)
    if not required <= actual or not actual <= allowed:
        raise ValueError("investigation_runtime_output_use_closure_changed")
    return expected


def _attempt_evidence(snapshot):
    """Account for every attempt, including noncompletion and missing outcomes."""
    lineage_status = "unavailable"
    for row in snapshot["original_records"]:
        record = _document(row["payload"].encode())
        if record["kind"] == "execution_event" and record.get("event") in {
                "execution_activated", "execution_resumed", "execution_cancelled"}:
            lineage_status = record["state"]["status"]
    receipts = {}
    for reference in snapshot.get("runtime_references", []):
        _verify_runtime_reference(snapshot, reference)
        if reference["role"] == "original_oci_receipt":
            identity = reference["attempt_id"]
            if identity in receipts:
                raise ValueError("investigation_runtime_receipt_duplicate")
            receipts[identity] = reference
    rows, seen = [], set()
    for attempt in sorted(snapshot["attempts"], key=lambda item: item["token"]["attempt_id"]):
        token = attempt["token"]
        identity = token["attempt_id"]
        if identity in seen:
            raise ValueError("investigation_attempt_identity_duplicate")
        seen.add(identity)
        outcome = snapshot["outcomes"].get(identity)
        receipt_sha = outcome["receipt_sha256"] if outcome is not None else None
        execution = attempt["execution_state"]
        if outcome is not None and token["backend_id"] == "local_exploratory_oci_1":
            if identity not in receipts or receipts[identity]["sha256"] != receipt_sha:
                raise ValueError("replay_required_runtime_receipt_owner_unavailable:" + identity)
            status, io = "bound_original_receipt", "required_via_bound_receipt"
        elif outcome is None and execution == "reserved" and attempt["external_job_id"] is None:
            status, io = "never_launched", "not_required_never_launched"
        elif (outcome is None and execution == "noncompletion_confirmed" and attempt["external_job_id"] is None
              and attempt.get("no_container_binding") is True):
            status, io = "never_launched_confirmed", "not_required_never_launched"
        elif outcome is None:
            status, io = "unavailable_pending_or_uncertain", "unavailable"
        else:
            status, io = "provider_receipt_unresolved", "unavailable"
        rows.append({"attempt_id": identity, "backend_id": token["backend_id"], "execution_state": execution,
            "acceptance_state": attempt["acceptance_state"], "cleanup_state": attempt["cleanup_state"],
            "lineage_execution_status": lineage_status, "outcome_status": outcome["status"] if outcome is not None else None,
            "receipt_sha256": receipt_sha, "observation_sha256": attempt["observation_sha256"],
            "receipt_status": status, "io_status": io})
    if set(snapshot["dispatches"]) - seen or set(snapshot["outcomes"]) - seen or set(receipts) - seen:
        raise ValueError("investigation_attempt_evidence_inventory_changed")
    uses = _runtime_output_use_closure(snapshot)
    for row in rows:
        row["output_uses"] = [use for use in uses if use["attempt_id"] == row["attempt_id"]]
        row["output_use_status"] = ("external_input_scope_excluded" if any(use["scope"] == "external_input" for use in row["output_uses"])
                                    else "bound_original_outputs" if row["output_uses"] else "no_outputs_recorded")
    return rows


def _attempt_evidence_complete(rows):
    return all(row["receipt_status"] in {"bound_original_receipt", "never_launched", "never_launched_confirmed"}
               and row["output_use_status"] != "external_input_scope_excluded" for row in rows)


def _dependent_replay_references(path, reference, content, snapshot):
    if reference["role"] == "multiple_uses":
        return [child for role in sorted(_reference_roles(reference)) for child in _dependent_replay_references(
            path, {**reference, "role": role}, content, snapshot)]
    if reference["role"] == "frozen_literature_snapshot":
        return [(item["path"], {"sha256": item["content_sha256"], "role": "frozen_literature_response"})
                for item in _document(content)["responses"]]
    if reference["role"] == "original_oci_receipt":
        from devbench.platform.adaptive_workflow import StepReceipt
        receipt = StepReceipt.model_validate_json(content)
        attempt_id = Path(path).parent.name
        dispatch, outcome = snapshot["dispatches"][attempt_id], snapshot["outcomes"][attempt_id]
        if (receipt.job_id != attempt_id or receipt.request_sha256 != dispatch["token"]["request_sha256"]
                or _sha(content) != outcome["receipt_sha256"] or receipt.io_sha256 is None):
            raise ValueError("investigation_original_oci_receipt_binding_changed")
        outputs = {**outcome.get("staged_outputs", {}), **outcome["outputs"]}
        if (receipt.model_dump(mode="json")["outputs"] != outputs or any(
                port in outcome["outputs"] and outcome["outputs"][port] != artifact
                for port, artifact in outcome.get("staged_outputs", {}).items())):
            raise ValueError("investigation_original_oci_receipt_output_mapping_changed")
        return [(str(Path(path).with_name("io.json")), {"sha256": receipt.io_sha256, "role": "original_oci_io"})]
    if reference["role"] == "original_oci_io":
        io = _document(content)
        attempt_id = Path(path).parent.name
        dispatch = snapshot["dispatches"][attempt_id]
        if (io["frozen"]["token"] != dispatch["token"] or io["inputs"] != dispatch["envelope"]["pins"]
                or io["outputs"] != {**snapshot["outcomes"][attempt_id].get("staged_outputs", {}),
                                     **snapshot["outcomes"][attempt_id]["outputs"]}):
            raise ValueError("investigation_original_oci_io_binding_changed")
    return []


def investigation_replay_package(registry, *, capture_entitlement, artifact_entitlements=None, planner=None, runtime_owner=None):
    from .bundles import REPLAY_MAX_BYTES
    from devbench.study.evidence import _read
    from .scientific_publication import PublicationRegistry
    if type(registry) is not PublicationRegistry:
        raise TypeError("Investigation replay requires the actual publication/workflow owner")
    snapshot = registry.workflow.replay_snapshot(entitlement=capture_entitlement, planner=planner)
    _validate_investigation_snapshot(snapshot, {"capture_identity": snapshot["capture_identity"]})
    if runtime_owner is not None:
        from devbench.engine.exploratory_oci import ExploratoryOCI
        if type(runtime_owner) is not ExploratoryOCI:
            raise PermissionError("Investigation replay requires its actual runtime owner")
        snapshot["runtime_references"] = runtime_owner.replay_references(registry.workflow)
    attempt_evidence = _attempt_evidence(snapshot)
    bindings, external, locks = _investigation_dependencies(snapshot)
    if any(item["role"] == "unresolved_derived_output" for item in external):
        raise ValueError("replay_required_derived_output_owner_unavailable")
    entitlements = {} if artifact_entitlements is None else artifact_entitlements
    excluded = _known_input_digests(snapshot)
    for reference in bindings.values():
        _reject_input_alias(reference, excluded)
        if reference["sha256"] in entitlements:
            _reject_input_alias(reference, excluded, content_sha256=_audit_entitlement(entitlements[reference["sha256"]]).content_sha256)
    members, logical = {"investigation.json": _canonical(snapshot)}, {}
    pending = list(bindings.items())
    while pending:
        path, reference = pending.pop(0)
        reference = {"uses": [], **reference}
        _reject_input_alias(reference, excluded)
        if path in logical:
            if any(logical[path][key] != reference[key] for key in ("sha256", "role", "uses")):
                raise ValueError("investigation_replay_reference_alias_changed")
            continue
        identity = reference["sha256"]
        if identity not in entitlements:
            raise PermissionError("replay_required_attachment_entitlement_unavailable:" + reference["role"])
        entitlement = _inert_entitlement(entitlements.get(identity))
        _reject_input_alias(reference, excluded, content_sha256=entitlement.content_sha256)
        if "frozen_literature_snapshot" in _reference_roles(reference):
            _require_replay_scope(entitlement, identity)
        else:
            entitlement.require(identity)
        content = _read(Path(path), REPLAY_MAX_BYTES)
        content_sha = _sha(content)
        _reject_input_alias(reference, excluded, content_sha256=content_sha)
        entitlement.require(content_sha)
        reference_sha = (_sha(_canonical(_document(content))) if "frozen_literature_snapshot" in _reference_roles(reference) else content_sha)
        if reference_sha != identity:
            raise ValueError("investigation_replay_original_reference_changed")
        member = "objects/" + content_sha
        members[member] = content
        logical[path] = {**reference, "content_sha256": content_sha, "member": member, "size_bytes": len(content)}
        pending.extend(_dependent_replay_references(path, reference, content, snapshot))
        if len(logical) > 1024:
            raise ValueError("investigation_replay_required_closure_exceeds_bound")
    if registry.workflow.replay_identity(planner=planner) != snapshot["capture_identity"]:
        raise ValueError("investigation_changed_during_capture")
    raw, receipt = _sealed_replay(members, {"format": INVESTIGATION_REPLAY_FORMAT, "audience": "restricted_audit",
        "capture_identity": snapshot["capture_identity"], "logical_bindings": logical,
        "external_dependencies": external, "scientific_locks": locks,
        "attempt_evidence": attempt_evidence,
        "original_input_scope": "excluded_separate_entitlement_required", "protected_evaluation_scope": "excluded",
        "scientific_acceptance": "not_granted", "current_use": "requires_live_owner",
        "numerical_replay": "unsupported_adaptive_profile"})
    receipt["required_runtime_evidence_complete"] = _attempt_evidence_complete(attempt_evidence)
    return raw, receipt


def verify_investigation_replay(package, expected_root, *, entitlement):
    entitlement = _inert_entitlement(entitlement)
    _require_replay_scope(entitlement, expected_root)
    (manifest, members), _ = _restricted_replay_members(package, expected_root, entitlement, INVESTIGATION_REPLAY_FORMAT)
    fields = {"format", "audience", "capture_identity", "logical_bindings", "external_dependencies", "scientific_locks", "attempt_evidence",
              "original_input_scope", "protected_evaluation_scope", "scientific_acceptance", "current_use", "numerical_replay", "members"}
    if (set(manifest) != fields or manifest["audience"] != "restricted_audit"
            or manifest["original_input_scope"] != "excluded_separate_entitlement_required"
            or manifest["protected_evaluation_scope"] != "excluded" or manifest["scientific_acceptance"] != "not_granted"
            or manifest["current_use"] != "requires_live_owner" or manifest["numerical_replay"] != "unsupported_adaptive_profile"):
        raise ValueError("investigation_replay_scope_invalid")
    snapshot = _document(members["investigation.json"])
    identity = _validate_investigation_snapshot(snapshot, manifest)
    attempt_evidence = _attempt_evidence(snapshot)
    if manifest["attempt_evidence"] != attempt_evidence:
        raise ValueError("investigation_attempt_receipt_inventory_changed")
    bindings, external, locks = _investigation_dependencies(snapshot)
    if manifest["external_dependencies"] != external or manifest["scientific_locks"] != locks:
        raise ValueError("investigation_scientific_dependencies_changed")
    logical = manifest["logical_bindings"]
    pending = list(bindings.items())
    checked = set()
    while pending:
        path, reference = pending.pop(0)
        reference = {"uses": [], **reference}
        if path in checked:
            continue
        binding = logical.get(path)
        if (binding is None or set(binding) != {"member", "sha256", "content_sha256", "size_bytes", "role", "uses"}
                or any(binding[key] != value for key, value in reference.items())
                or binding["member"] != "objects/" + binding["content_sha256"]
                or manifest["members"].get(binding["member"]) != {"sha256": binding["content_sha256"], "size_bytes": binding["size_bytes"]}
                or "frozen_literature_snapshot" not in _reference_roles(reference) and binding["sha256"] != binding["content_sha256"]):
            raise ValueError("investigation_required_logical_dependency_missing")
        checked.add(path)
        pending.extend(_dependent_replay_references(path, reference, members[binding["member"]], snapshot))
    if set(logical) != checked or set(members) != {"investigation.json", "replay-manifest.json", *(item["member"] for item in logical.values())}:
        raise ValueError("investigation_unregistered_attachment")
    from .literature_adapter import verify_frozen_snapshot
    def resolve(path, identity, maximum):
        reference = logical.get(path)
        if reference is None or reference["sha256"] != identity or reference["size_bytes"] > maximum:
            raise ValueError("investigation_frozen_dependency_unavailable")
        return members[reference["member"]]
    literature = snapshot["literature"]
    for reference in literature["snapshots"].values():
        verify_frozen_snapshot(reference, resolve, limits=literature["grant"]["limits"],
                               implementation_sha256=literature["grant"]["implementation_sha256"])
    return {"verified": True, "root_sha256": expected_root, "mode": "investigation_evidence_verification",
        "current_use": "unavailable", "scientific_acceptance": "not_granted", "numerical_replay": "unsupported_adaptive_profile",
        "original_records": len(snapshot["original_records"]), "attempts": len(snapshot["attempts"]),
        "external_input_dependencies": len(external), "numerical_stage_launches": 0, "model_sessions": 0,
        "literature_snapshots_verified": len(literature["snapshots"]),
        "attempt_evidence": attempt_evidence,
        "required_runtime_evidence_complete": _attempt_evidence_complete(attempt_evidence),
        "evidence_completeness": "included_evidence_with_explicit_external_inputs" if _attempt_evidence_complete(attempt_evidence)
                                 else "incomplete_runtime_receipt_evidence",
        "scientific_material_status": "bound_original_owner_capture" if identity.get("scientific_material_binding") is not None
                                      else "unavailable_no_owner_binding"}


def _validate_investigation_snapshot(snapshot, manifest):
    """One inert owner-metadata gate for capture, preflight and verification."""
    from devbench.engine.scientific_authority import _record_payload
    from devbench.engine.workflow_operations import WorkflowOperationLedger
    identity = snapshot["capture_identity"]
    if (manifest["capture_identity"] != identity or _sha(_canonical({k: v for k, v in identity.items() if k != "sha256"})) != identity["sha256"]
            or identity["records"] != [{"revision": row["revision"], "sha256": row["sha256"]} for row in snapshot["original_records"]]):
        raise ValueError("investigation_original_record_inventory_changed")
    original_records = []
    for row in snapshot["original_records"]:
        record = _document(row["payload"].encode())
        if (_record_payload(record) != row["payload"] or _sha(row["payload"].encode()) != row["sha256"]
                or record["record_id"] != row["record_id"] or record["kind"] != row["kind"]):
            raise ValueError("investigation_original_record_changed")
        original_records.append({"revision": row["revision"], "record": record})
    _verify_investigation_projection(snapshot, original_records)
    for key in ("model_campaigns", "model_reservations"):
        fields = ("campaign_id",) if key == "model_campaigns" else ("campaign_id", "node", "attempt")
        captured = _model_replay_index(identity[key], fields)
        observed = _model_replay_index(snapshot[key], fields)
        if (captured.keys() != observed.keys() or any(not reference.keys() <= observed[token].keys()
                or any(observed[token][name] != value for name, value in reference.items())
                for token, reference in captured.items())):
            raise ValueError("investigation_model_capture_changed")
    for record in snapshot["model_reservations"]:
        for name in ("request", "limits", "capture"):
            expected = record[name + "_digest"]
            actual = _sha(_canonical(record[name])) if record[name] is not None else None
            if actual != expected:
                raise ValueError("investigation_original_model_body_changed:" + name)
    for campaign in snapshot["model_campaigns"]:
        if campaign["session_id"] != snapshot["lineage_id"]:
            raise ValueError("investigation_model_campaign_owner_changed")
        for name in ("policy", "capability", "identity"):
            if _sha(_canonical(campaign[name])) != campaign[name + "_digest"]:
                raise ValueError("investigation_original_model_policy_changed")
        records = [record for record in snapshot["model_reservations"] if record["campaign_id"] == campaign["campaign_id"]]
        totals = WorkflowOperationLedger.replay_model_totals(campaign, records)
        if any(campaign.get(key) != value for key, value in totals.items()):
            raise ValueError("investigation_original_model_usage_changed")
    if snapshot["model_campaign"] != (snapshot["model_campaigns"][0] if len(snapshot["model_campaigns"]) == 1 else None):
        raise ValueError("investigation_model_campaign_alias_changed")
    exposure = snapshot["exposures"]
    if set(exposure) != {"contexts", "policies", "sources", "disclosures", "original_records"}:
        raise ValueError("investigation_exposure_inventory_changed")
    if identity["access_records"] != {kind: {key: item["sha256"] for key, item in values.items()}
                                    for kind, values in exposure["original_records"].items()}:
        raise ValueError("investigation_original_access_identity_changed")
    for kind in ("contexts", "policies", "sources", "disclosures"):
        originals = exposure["original_records"].get(kind, {})
        if set(originals) != set(exposure[kind]):
            raise ValueError("investigation_original_access_inventory_changed")
        for key, record in originals.items():
            value = _document(record["payload"].encode())
            if _sha(record["payload"].encode()) != record["sha256"] or value != exposure[kind][key]:
                raise ValueError("investigation_original_access_projection_changed")
    return identity


def _model_replay_index(records, fields):
    if type(records) is not list or len(records) > 4096:
        raise ValueError("investigation_model_inventory_invalid")
    indexed = {}
    for record in records:
        if type(record) is not dict:
            raise ValueError("investigation_model_identity_invalid")
        values = tuple(record.get(field) for field in fields)
        if any((type(value) is not int or not 0 <= value <= 2147483647) if field == "attempt"
               else (type(value) is not str or not 0 < len(value) <= 256)
               for field, value in zip(fields, values)):
            raise ValueError("investigation_model_identity_invalid")
        if values in indexed:
            raise ValueError("investigation_model_identity_duplicate")
        indexed[values] = record
    return indexed


def _verify_investigation_projection(snapshot, original_records):
    """Compare owner-folded capture state with the unchanged original record stream."""
    from devbench.platform.adaptive_workflow import _State
    from .scientific_review_holds import ReviewHoldLifecycle
    from .scientific_publication import PublicationRegistry
    from .literature_adapter import replay_literature_records
    state, attempts, disclosures = _State(), {}, {}
    review, literature, publications = [], [], []
    for row in original_records:
        record = row["record"]
        if record["kind"] == "adaptive_event":
            state.apply(record["event"], record["state"])
        elif record["kind"] == "disclosure_event":
            disclosures[record["state"]["reservation_id"]] = record["state"]
        elif record["kind"] == "execution_event":
            if record["record_id"].startswith("review-"):
                review.append(row)
            elif record["record_id"].startswith("literature-"):
                literature.append(record)
            elif record["record_id"].startswith("publication-subject-"):
                publications.append(record)
            value = record["state"]
            attempt = value.get("attempt", value)
            if type(attempt) is dict and type(attempt.get("token")) is dict:
                attempts[attempt["token"]["attempt_id"]] = attempt
    expected = {"dispatches": state.dispatches, "outcomes": state.outcomes, "controller": state.controller,
        "basis": state.basis, "retired": state.retired, "sweeps": state.sweeps,
        "steps": {key: {"specification": node.model_dump(mode="json"), "generation": state.generations[key]}
                  for key, node in sorted(state.nodes.items())},
        "artifacts": {key: {"artifact": value, "producer": producer} for key, (value, producer) in state.artifacts.items()},
        "tombstones": [{"step_id": key[0], "generation": key[1], "reason": value}
                       for key, value in sorted(state.tombstones.items())],
        "claim_stale": [{"step_id": key[0], "generation": key[1], "reason": value}
                         for key, value in sorted(state.claim_stale.items())],
        "source_basis": state.sources, "holds": state.holds,
        "stale_versions": sorted(state.stale_versions), "comparison_versions": sorted(state.comparison_versions),
        "reviews": ReviewHoldLifecycle.replay_records(review), "literature": replay_literature_records(literature)}
    subjects, heads = PublicationRegistry.replay_subjects(publications, snapshot["lineage_id"])
    expected.update(publication_subjects={key: value.model_dump(mode="json") for key, value in subjects.items()},
                    publication_heads=heads)
    for key, value in expected.items():
        if _canonical(value) != _canonical(snapshot[key]):
            raise ValueError("investigation_original_projection_changed:" + key)
    material = snapshot.get("scientific_material")
    binding = snapshot["capture_identity"].get("scientific_material_binding")
    if material is None:
        if binding is not None:
            raise ValueError("investigation_scientific_material_missing")
    else:
        if (type(material) is not dict or set(material) != {"plan", "basis", "retired", "sweeps", "steps"}
                or type(binding) is not dict or set(binding) != {"format", "lineage_id", "plan_sha256", "objective_sha256", "material_sha256"}
                or binding["format"] != "devbench.scientific-material-capture/1" or binding["lineage_id"] != snapshot["lineage_id"]
                or material["plan"]["session_id"] != snapshot["lineage_id"]
                or _sha(_canonical(material["plan"])) != binding["plan_sha256"]
                or _sha(_canonical(material["plan"]["context"]["task"])) != binding["objective_sha256"]
                or _sha(_canonical(material)) != binding["material_sha256"]):
            raise ValueError("investigation_original_scientific_material_binding_changed")
        original_material = {"basis": state.basis, "retired": state.retired, "sweeps": state.sweeps,
            "steps": {key: {"step": node.model_dump(mode="json"), "generation": state.generations[key], "outcome": state.outcome(key)}
                      for key, node in sorted(state.nodes.items())}}
        if any(_canonical(material[key]) != _canonical(value) for key, value in original_material.items()):
            raise ValueError("investigation_scientific_material_disagrees_with_original_records")
    for attempt in snapshot["attempts"]:
        if attempts.get(attempt["token"]["attempt_id"]) != attempt:
            raise ValueError("investigation_original_attempt_changed")
    if len(attempts) != len(snapshot["attempts"]):
        raise ValueError("investigation_original_attempt_inventory_changed")
    if any(snapshot["exposures"]["disclosures"].get(identity) != record for identity, record in disclosures.items()):
        raise ValueError("investigation_original_exposure_changed")
