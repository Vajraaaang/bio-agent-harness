"""Capture and verify exact evidence from a fixed RNA capsule."""
from __future__ import annotations

from pathlib import Path

from ._replay_archive import (
    _SHA, _audit_entitlement, _canonical, _document, _inert_entitlement, _replay_name,
    _require_replay_scope, _restricted_replay_members, _sha, _zip_replay,
)


REPLAY_FORMAT = "devbench.rna-replay-package/1"


_REPLAY_COMPARISON = {"kind": "engineering_same_image", "absolute_tolerance": 1e-14,
                      "relative_tolerance": 1e-10, "scientific_tolerance_ratified": False}


def verify_replay_package(package, expected_root, *, entitlement=None, current_guard=None):
    """Verify retained evidence only; never refit, fetch or construct execution state."""
    _audit_entitlement(entitlement)
    return _load_replay(package, expected_root, entitlement, current_guard)[0]


_ERQL_ARTIFACTS = {"plan.json", "approvals.json", "trust-registry.json", "request.json", "erql_1_1.R",
    "erql-package-sources.lock.json", "transport.json", "lifecycle.jsonl", "stdout.log", "stderr.log", "result.json",
    "family-evidence.json", "family-synthesis.json", "outcome.json"}
_REPLAY_FIELDS = {"format", "scope", "audience", "original_root_sha256", "original_status", "plan_sha256",
    "profile", "family_allocation_sha256", "source_binding", "environment", "external_inputs", "artifacts",
    "original_reviews", "original_exposures", "literature", "plot_inputs", "required_dependencies",
    "excluded_scopes", "members", "publication_eligible", "numerical_comparison", "unsupported"}


def _fixed_replay_projection(plan, outcome, original_root, references, family_bytes, *, legacy_figure=False):
    """Pure presentation of the original signed family evidence; no fitting."""
    from .scientific_publication import build_publication_descriptor, PublicationAccess, fixed_capsule
    from devbench.core.procedure_profile import ProcedureProfile
    from devbench.core.publication_families import FamilyEvidence

    evidence = () if family_bytes is None else (FamilyEvidence.model_validate_json(family_bytes),)
    profile = ProcedureProfile(profile_id=plan.profile, version=1, operations=({"operation_id": "fixed_capsule",
        "output_kinds": ["table", "figure"], "max_memory_mb": plan.max_memory_mb, "timeout_seconds": plan.timeout_sec},),
        input_representations=("COUNT-1",), biological_units=("declared_biological_unit",), permitted_claims=("exploratory_finding",))
    descriptor = build_publication_descriptor(profile,
        source_sha256=outcome.get("family_evidence", {}).get("sha256", original_root),
        allocation=plan.allocation, access=PublicationAccess(source=True, outcomes=True), exploratory=True,
        dependency_sha256=tuple(sorted({original_root, *(row["sha256"] for row in references)})))
    return fixed_capsule(descriptor, plan.allocation, evidence, _legacy_figure=legacy_figure)


def _erql_replay_package(capsule, output, *, entitlement=None, snapshot_root=None):
    """Capture a fixed capsule's audit closure, excluding original numerical inputs."""

    from .bundles import REPLAY_MAX_BYTES
    from devbench.engine.execution_snapshot import portable_snapshot
    from devbench.engine.workspace import runs_root
    from devbench.study.evidence import _read
    from .checkpoints import bounded_inventory
    from .scientific_plot_revisions import fixed_renderer_identity

    capture = _inert_entitlement(entitlement)
    capture.require(capture.content_sha256)
    output = Path(output)
    manifest_bytes = _read(output / "audit/manifest.json", REPLAY_MAX_BYTES)
    capture.require(_sha(manifest_bytes))
    original = _document(manifest_bytes)
    root = original["root_hash"]
    references = original["artifacts"]
    if (any(row["kind"] != "file" or row["name"] not in _ERQL_ARTIFACTS for row in references)
            or len({row["name"] for row in references}) != len(references)):
        raise ValueError("replay_unsupported_original_artifact")
    artifacts = {row["name"]: {"member": "audit/objects/" + row["sha256"], "sha256": row["sha256"],
                               "size_bytes": row["size_bytes"]} for row in references}
    request = artifacts.pop("request.json")
    if request["sha256"] != capsule.plan.request_sha256:
        raise ValueError("replay_original_input_binding_changed")
    excluded_object = "objects/" + request["sha256"]
    # The external numerical input is not opened, including its duplicated DAG object.
    allowed = {"manifest.json", "ledger.json", "root.sha256", excluded_object,
               *("nodes/" + identity + ".json" for identity in original["node_ids"]),
               *("objects/" + row["sha256"] for row in references)}
    audit = bounded_inventory(output / "audit", omit=(excluded_object,), allowed=allowed)
    if audit["manifest.json"] != manifest_bytes:
        raise ValueError("replay_original_manifest_changed")
    members = {"audit/" + name: raw for name, raw in audit.items()}
    members["original-receipt.json"] = _read(output / "receipt.json", REPLAY_MAX_BYTES)
    outcome = _document(members[artifacts["outcome.json"]["member"]])
    binding = outcome["execution_code"]
    source = portable_snapshot(binding, runs_root() if snapshot_root is None else snapshot_root)
    members.update({"source/" + name: raw for name, raw in source.items()})
    plan = capsule.plan
    family_bytes = members[artifacts["family-evidence.json"]["member"]] if "family-evidence.json" in artifacts else None
    members["presentation.json"] = _canonical(_fixed_replay_projection(plan, outcome, root, references, family_bytes))
    members["renderer.json"] = _canonical(fixed_renderer_identity())
    plan = capsule.plan
    environment = {"image_id": plan.runtime_image_id, "platform": plan.platform,
                   "driver_sha256": plan.driver_sha256, "package_lock_sha256": plan.package_lock_sha256}
    dependencies = sorted({root, plan.sha256(), plan.source_sha256, plan.request_sha256,
                           plan.design_sha256, plan.allocation.sha256(), *(row["sha256"] for row in references)})
    manifest = {"format": REPLAY_FORMAT, "scope": "synthetic_verification", "audience": "restricted_audit",
        "original_root_sha256": root, "original_status": outcome["status"], "plan_sha256": plan.sha256(),
        "profile": plan.profile, "family_allocation_sha256": plan.allocation.sha256(), "source_binding": binding,
        "environment": environment, "external_inputs": [{"logical_id": "numerical_request",
            "sha256": request["sha256"], "size_bytes": request["size_bytes"], "role": "original_input",
            "included": False, "original_member": request["member"]}], "artifacts": artifacts,
        "original_reviews": {"signed_plan_approvals": artifacts["approvals.json"], "scientific_acceptance": "not_recorded"},
        "original_exposures": {"status": "not_recorded"}, "literature": {"status": "not_recorded", "members": []},
        "plot_inputs": {"geometry": "presentation.json", "renderer": "renderer.json"},
        "required_dependencies": dependencies, "excluded_scopes": ["original_input", "protected_evaluation"],
        "publication_eligible": False,
        "numerical_comparison": dict(_REPLAY_COMPARISON),
        "unsupported": ["adaptive_numerical_replay", "fresh_controller_run", "public_R0_release", "scientific_qualification"],
        "members": {name: {"sha256": _sha(raw), "size_bytes": len(raw)} for name, raw in sorted(members.items())}}
    # No alternate name may smuggle an excluded object into the retained closure.
    if any(_sha(raw) == request["sha256"] for raw in members.values()):
        raise ValueError("replay_original_input_alias")
    members["replay-manifest.json"] = _canonical(manifest)
    raw = _zip_replay(members)
    _verify_replay_members(members, _sha(members["replay-manifest.json"]))
    return raw, {"format": REPLAY_FORMAT, "root_sha256": _sha(members["replay-manifest.json"]),
                 "archive_sha256": _sha(raw), "archive_bytes": len(raw), "scope": "synthetic_verification",
                 "audience": "restricted_audit", "publication_eligible": False}


def describe_erql_replay(capsule, output, *, entitlement=None, snapshot_root=None):
    return _erql_replay_package(capsule, output, entitlement=entitlement, snapshot_root=snapshot_root)[1]


def export_erql_replay(capsule, output, destination, *, entitlement=None, capture_entitlement=None, snapshot_root=None, audience="restricted_audit"):
    from devbench.engine.workspace import protected_path
    if audience != "restricted_audit":
        raise PermissionError("synthetic_replay_has_no_admitted_public_package")
    entitlement = _audit_entitlement(entitlement)
    entitlement.require(entitlement.content_sha256)
    destination = protected_path(destination)
    if destination.exists() or not destination.parent.is_dir():
        raise ValueError("Replay export requires a new file in existing protected storage")
    raw, descriptor = _erql_replay_package(capsule, output, entitlement=capture_entitlement, snapshot_root=snapshot_root)
    released = entitlement.release(raw)
    with destination.open("xb") as stream:
        destination.chmod(0o600)
        stream.write(released)
    return descriptor


def _verify_replay_members(members, expected_root):
    from .bundles import REPLAY_MAX_BYTES
    from devbench.engine.execution_snapshot import verify_portable_snapshot
    from devharness_controlled.erql_contracts import parse_erql_plan, CANONICAL, ERQLFamilyPlan

    if (type(expected_root) is not str or not _SHA.fullmatch(expected_root)
            or _sha(members["replay-manifest.json"]) != expected_root):
        raise ValueError("replay_root_mismatch")
    manifest = _document(members["replay-manifest.json"])
    if (type(manifest) is not dict or set(manifest) != _REPLAY_FIELDS
            or manifest["format"] != REPLAY_FORMAT or manifest["scope"] != "synthetic_verification"
            or manifest["audience"] != "restricted_audit" or manifest["publication_eligible"] is not False
            or set(members) != set(manifest["members"]) | {"replay-manifest.json"}):
        raise ValueError("replay_manifest_or_inventory_invalid")
    for name, reference in manifest["members"].items():
        if (not _replay_name(name) or reference != {"sha256": _sha(members[name]), "size_bytes": len(members[name])}):
            raise ValueError("replay_member_binding_changed")
    artifacts = manifest["artifacts"]
    if set(artifacts) - (_ERQL_ARTIFACTS - {"request.json"}):
        raise ValueError("replay_unsupported_artifact")
    def retained(name):
        row = artifacts[name]
        if (set(row) != {"member", "sha256", "size_bytes"} or row["member"] != "audit/objects/" + row["sha256"]
                or manifest["members"].get(row["member"]) != {k: row[k] for k in ("sha256", "size_bytes")}):
            raise ValueError("replay_artifact_dependency_changed")
        return _document(members[row["member"]])
    plan = parse_erql_plan(retained("plan.json"))
    outcome = retained("outcome.json")
    if (not isinstance(plan, ERQLFamilyPlan) or plan.profile != CANONICAL or plan.sha256() != manifest["plan_sha256"]
            or plan.profile != manifest["profile"] or plan.allocation.sha256() != manifest["family_allocation_sha256"]
            or outcome["plan_sha256"] != plan.sha256() or outcome["status"] != manifest["original_status"]
            or outcome["execution_code"] != manifest["source_binding"]):
        raise ValueError("replay_scientific_specification_changed")
    external = manifest["external_inputs"]
    if (type(external) is not list or len(external) != 1 or set(external[0]) != {
            "logical_id", "sha256", "size_bytes", "role", "included", "original_member"}
            or external[0]["logical_id"] != "numerical_request" or external[0]["role"] != "original_input"
            or external[0]["included"] is not False or external[0]["sha256"] != plan.request_sha256
            or type(external[0]["size_bytes"]) is not int or not 0 < external[0]["size_bytes"] <= REPLAY_MAX_BYTES
            or external[0]["original_member"] != "audit/objects/" + plan.request_sha256
            or any(_sha(raw) == plan.request_sha256 for raw in members.values())):
        raise ValueError("replay_external_input_partition_changed")
    original = _document(members["audit/manifest.json"])
    original_root = manifest["original_root_sha256"]
    if (_sha(_canonical({k: v for k, v in original.items() if k != "root_hash"})) != original_root
            or original["root_hash"] != original_root or original["analysis_fingerprint"] != plan.sha256()
            or members["audit/root.sha256"] != (original_root + "\n").encode()
            or _document(members["original-receipt.json"])["root_hash"] != original_root):
        raise ValueError("replay_original_root_changed")
    expected_artifacts = {row["name"]: {"member": "audit/objects/" + row["sha256"],
        "sha256": row["sha256"], "size_bytes": row["size_bytes"]} for row in original["artifacts"] if row["name"] != "request.json"}
    requests = [row for row in original["artifacts"] if row["name"] == "request.json"]
    if (artifacts != expected_artifacts or len(requests) != 1 or requests[0]["sha256"] != plan.request_sha256
            or requests[0]["size_bytes"] != external[0]["size_bytes"]):
        raise ValueError("replay_original_dependency_closure_changed")
    for name in artifacts:
        row = artifacts[name]
        if manifest["members"].get(row["member"]) != {k: row[k] for k in ("sha256", "size_bytes")}:
            raise ValueError("replay_original_artifact_missing")
    ids, seen = original["node_ids"], set()
    if len(ids) != original["node_count"] or len(ids) != len(set(ids)):
        raise ValueError("replay_original_nodes_invalid")
    for index, identity in enumerate(ids):
        node = _document(members["audit/nodes/" + identity + ".json"])
        if (_sha(_canonical({k: v for k, v in node.items() if k != "node_id"})) != identity
                or node["node_id"] != identity or node["run_id"] != original["run_id"]
                or node["sequence"] != index + 1 or node["previous"] != (ids[index - 1] if index else None)
                or any(parent not in seen for parent in node["parents"])):
            raise ValueError("replay_original_node_changed")
        seen.add(identity)
    expected_audit = {"audit/manifest.json", "audit/ledger.json", "audit/root.sha256",
                      *("audit/nodes/" + identity + ".json" for identity in ids),
                      *(row["member"] for row in artifacts.values())}
    if {name for name in members if name.startswith("audit/")} != expected_audit:
        raise ValueError("replay_original_inventory_changed")
    environment = {"image_id": plan.runtime_image_id, "platform": plan.platform,
                   "driver_sha256": plan.driver_sha256, "package_lock_sha256": plan.package_lock_sha256}
    if manifest["environment"] != environment:
        raise ValueError("replay_environment_changed")
    if manifest["numerical_comparison"] != _REPLAY_COMPARISON:
        raise ValueError("replay_numerical_comparison_changed")
    source = {name.removeprefix("source/"): raw for name, raw in members.items() if name.startswith("source/")}
    verify_portable_snapshot(source, manifest["source_binding"]["sha256"])
    if (manifest["original_reviews"] != {"signed_plan_approvals": artifacts["approvals.json"], "scientific_acceptance": "not_recorded"}
            or manifest["original_exposures"] != {"status": "not_recorded"}
            or manifest["literature"] != {"status": "not_recorded", "members": []}
            or manifest["plot_inputs"] != {"geometry": "presentation.json", "renderer": "renderer.json"}
            or manifest["excluded_scopes"] != ["original_input", "protected_evaluation"]):
        raise ValueError("replay_captured_state_changed")
    expected_dependencies = sorted({original_root, plan.sha256(), plan.source_sha256, plan.request_sha256,
        plan.design_sha256, plan.allocation.sha256(), *(row["sha256"] for row in original["artifacts"])})
    if manifest["required_dependencies"] != expected_dependencies:
        raise ValueError("replay_required_dependencies_changed")
    if set(members) != expected_audit | {"source/" + name for name in source} | {
            "original-receipt.json", "presentation.json", "renderer.json", "replay-manifest.json"}:
        raise ValueError("replay_unregistered_attachment")
    publication = _document(members["presentation.json"])
    body = {key: value for key, value in publication.items() if key != "publication_sha256"}
    if _sha(_canonical(body)) != publication["publication_sha256"]:
        raise ValueError("replay_presentation_binding_changed")
    family_bytes = members[artifacts["family-evidence.json"]["member"]] if "family-evidence.json" in artifacts else None
    from .scientific_publication import _uses_legacy_fixed_figure

    expected_publication = _fixed_replay_projection(plan, outcome, original_root, original["artifacts"], family_bytes,
        legacy_figure=_uses_legacy_fixed_figure(publication.get("figure")))
    if _canonical(publication) != _canonical(expected_publication):
        raise ValueError("replay_presentation_evidence_mismatch")
    return manifest


def _load_replay(package, expected_root, entitlement, current_guard=None):
    from .scientific_publication import PublicationGuard
    entitlement = _inert_entitlement(entitlement)
    _require_replay_scope(entitlement, expected_root)
    (_, members), archive_sha = _restricted_replay_members(package, expected_root, entitlement, REPLAY_FORMAT)
    manifest = _verify_replay_members(members, expected_root)
    current = "unavailable"
    if current_guard is not None:
        if type(current_guard) is not PublicationGuard or not current_guard.roots:
            raise PermissionError("replay_requires_live_current_use_resolver")
        _replay_current_use(current_guard, manifest["required_dependencies"])
        current = "checked_evaluation_only"
    return {"verified": True, "operation_kind": "evidence_verification", "root_sha256": expected_root,
            "archive_sha256": archive_sha, "members": len(members), "original_status": manifest["original_status"],
            "current_use": current, "source_data_verification": "incomplete_external_input_omitted",
            "publication_eligible": False, "qualification": "blocked", "numerical_stage_launches": 0,
            "model_sessions": 0, "unsupported": manifest["unsupported"]}, manifest, members


def _replay_current_use(guard, dependencies):
    from .scientific_publication import PublicationGuard, workspace_guard
    # The supplied roots are live service locations, never archive path fields.
    PublicationGuard(guard.blocked_hashes, available=guard.available).require(*dependencies)
    for root in guard.roots:
        workspace_guard(root, _snapshot=True, inert=True).require(*dependencies)


def replay_package_presentation(package, expected_root, output, *, entitlement=None, current_guard=None):
    from devbench.engine.workspace import protected_path
    from .scientific_plot_revisions import render_fixed_replay
    report, manifest, members = _load_replay(package, expected_root, entitlement, current_guard)
    output = protected_path(output)
    if output.exists() or not output.parent.is_dir():
        raise ValueError("Presentation replay requires a fresh protected output")
    rendered = render_fixed_replay(_document(members["presentation.json"]), _document(members["renderer.json"]))
    report = {**report, "operation_kind": "historical_presentation",
              "presentation_sha256": {name: _sha(raw) for name, raw in rendered.items()}}
    _inert_entitlement(entitlement).require(report["archive_sha256"])
    if current_guard is not None:
        _replay_current_use(current_guard, manifest["required_dependencies"])
    output.mkdir(mode=0o700)
    for name, raw in {**rendered, "replay-receipt.json": _canonical(report)}.items():
        with (output / name).open("xb") as stream:
            (output / name).chmod(0o600)
            stream.write(raw)
    return report
