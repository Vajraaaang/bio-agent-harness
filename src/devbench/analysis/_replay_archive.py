"""Bounded archive admission, member custody and public replay."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import io
import os
from pathlib import Path
import re
import stat
import struct
import zipfile

from devbench.core._hashing import contract_json_bytes as _canonical
from devbench.core._hashing import decode_receipt_json as _document


INVESTIGATION_REPLAY_FORMAT = "devbench.investigation-replay-package/1"
PUBLIC_REPLAY_FORMAT = "devbench.publication-replay-package/1"


_SHA = re.compile(r"[a-f0-9]{64}\Z")


def _replay_name(name):
    return (type(name) is str and bool(name) and len(name) <= 512 and "\\" not in name
            and ":" not in name and "\x00" not in name and not name.startswith("/")
            and all(part not in {"", ".", ".."} for part in name.split("/")))


def _replay_archive(raw):
    """Decode a bounded ZIP in memory. Verification never extracts archive code."""

    from .bundles import REPLAY_MAX_BYTES, REPLAY_MAX_EXPANDED, REPLAY_MAX_MEMBERS

    if type(raw) is not bytes or len(raw) > REPLAY_MAX_BYTES:
        raise ValueError("replay_archive_byte_bound")
    result = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if len(entries) > REPLAY_MAX_MEMBERS or sum(e.file_size for e in entries) > REPLAY_MAX_EXPANDED:
            raise ValueError("replay_archive_inventory_bound")
        for entry in entries:
            mode = entry.external_attr >> 16
            if (not _replay_name(entry.filename) or entry.filename in result or entry.is_dir()
                    or entry.flag_bits & 1 or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                    or entry.file_size > REPLAY_MAX_BYTES):
                raise ValueError("replay_unsafe_archive_member")
            with archive.open(entry) as stream:
                content = stream.read(REPLAY_MAX_BYTES + 1)
            if len(content) != entry.file_size or len(content) > REPLAY_MAX_BYTES:
                raise ValueError("replay_archive_size_mismatch")
            result[entry.filename] = content
    return result


def _audit_entitlement(entitlement):
    from .scientific_publication import AuditReplayEntitlement
    from .evidence_access import EvidenceDelivery, EvidenceBroker
    if (type(entitlement) is not AuditReplayEntitlement or type(entitlement.delivery) is not EvidenceDelivery
            or type(entitlement.delivery.broker) is not EvidenceBroker
            or type(entitlement.content_sha256) is not str or not _SHA.fullmatch(entitlement.content_sha256)):
        raise PermissionError("replay_requires_current_audit_entitlement")
    return entitlement


def _inert_entitlement(entitlement):
    """Open the separately supplied checkpointed audit authority without sidecars."""
    from .evidence_access import EvidenceBroker
    from .scientific_publication import AuditReplayEntitlement
    from devbench.engine.workflow_operations import WorkflowOperationLedger
    entitlement = _audit_entitlement(entitlement)
    delivery = entitlement.delivery
    ledger = WorkflowOperationLedger(delivery.broker.authority.ledger.path, readonly=True, inert=True)
    broker = EvidenceBroker.open_readonly(ledger.scientific)
    return AuditReplayEntitlement(broker.delivery(delivery.context_id, delivery.source_id), entitlement.content_sha256)


def _require_replay_scope(entitlement, expected_root):
    """Check current role and the externally retained root before opening bytes."""
    entitlement = _audit_entitlement(entitlement)
    entitlement.require(entitlement.content_sha256)
    delivery = entitlement.delivery
    with delivery.broker.authority.ledger._database() as db:
        source = delivery.broker._source(db, delivery.source_id)
        if expected_root not in source["roots"]:
            raise PermissionError("replay_audit_entitlement_root_mismatch")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _zip_replay(members):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for name, raw in sorted(members.items()):
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.external_attr = (stat.S_IFREG | 0o600) << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, raw)
    raw = stream.getvalue()
    _replay_archive(raw)
    return raw


def _sealed_replay(members, document):
    from .bundles import REPLAY_MAX_BYTES

    if any(len(raw) > REPLAY_MAX_BYTES for raw in members.values()):
        raise ValueError("replay_required_member_exceeds_bound")
    manifest = {**document, "members": {name: {"sha256": _sha(raw), "size_bytes": len(raw)}
                                       for name, raw in sorted(members.items())}}
    raw_manifest = _canonical(manifest)
    raw = _zip_replay({**members, "replay-manifest.json": raw_manifest})
    return raw, {"format": document["format"], "root_sha256": _sha(raw_manifest), "archive_sha256": _sha(raw),
                 "archive_bytes": len(raw), "audience": document["audience"]}


def _checked_replay_members(raw, expected_root, format):
    members = _replay_archive(raw)
    return _checked_decoded_members(members, expected_root, format)


def _checked_decoded_members(members, expected_root, format):
    if _sha(members["replay-manifest.json"]) != expected_root:
        raise ValueError("replay_root_mismatch")
    manifest = _document(members["replay-manifest.json"])
    if manifest.get("format") != format or set(members) != set(manifest["members"]) | {"replay-manifest.json"}:
        raise ValueError("replay_inventory_or_format_mismatch")
    for name, identity in manifest["members"].items():
        if identity != {"sha256": _sha(members[name]), "size_bytes": len(members[name])}:
            raise ValueError("replay_member_binding_changed")
    return manifest, members


@contextmanager
def _public_archive_stream(package):
    """Open bounded bytes for metadata seeks, never a bulk unentitled read."""

    from .bundles import REPLAY_MAX_BYTES

    if type(package) is bytes:
        if len(package) > REPLAY_MAX_BYTES:
            raise ValueError("replay_archive_byte_bound")
        with io.BytesIO(package) as stream:
            yield stream, len(package)
        return
    path = Path(package)
    if (not path.is_absolute() or ".." in path.parts
            or any(part.is_symlink() for part in (path, *path.parents))):
        raise ValueError("Public replay requires a regular absolute archive")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > REPLAY_MAX_BYTES:
            raise ValueError("replay_archive_byte_bound_or_link")
        def signature(value):
            return (value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        try:
            yield stream, before.st_size
        finally:
            if signature(before) != signature(os.fstat(stream.fileno())) or signature(before) != signature(path.lstat()):
                raise ValueError("replay_archive_changed_during_read")


def _public_replay_members(package, expected_root):
    """Authenticate the closed public manifest before any payload decoding."""

    from .bundles import REPLAY_MAX_BYTES, REPLAY_MAX_EXPANDED, REPLAY_MAX_MEMBERS

    allowed = {"replay-manifest.json", "publication.json", "release-witness.json", "renderer.json"}
    with _public_archive_stream(package) as (stream, size):
        if size < 22:
            raise ValueError("public_replay_archive_invalid")
        # Our packages have no ZIP comments, prefixes, split volumes or ZIP64.
        # Checking the fixed footer avoids zipfile's broad reverse comment scan.
        stream.seek(size - 22)
        footer = struct.unpack("<4s4H2LH", stream.read(22))
        marker, disk, directory_disk, count_disk, count, directory_bytes, offset, comment = footer
        if (marker != b"PK\x05\x06" or disk or directory_disk or comment or count != count_disk
                or not 1 <= count <= REPLAY_MAX_MEMBERS or offset + directory_bytes != size - 22):
            raise ValueError("public_replay_archive_metadata_invalid")
        with zipfile.ZipFile(stream) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if (len(entries) != count or len(set(names)) != count
                    or sum(entry.file_size for entry in entries) > REPLAY_MAX_EXPANDED):
                raise ValueError("public_replay_inventory_invalid")
            for entry in entries:
                mode = entry.external_attr >> 16
                if (not _replay_name(entry.filename) or entry.is_dir() or entry.flag_bits & 1
                        or stat.S_IFMT(mode) not in (0, stat.S_IFREG) or entry.file_size > REPLAY_MAX_BYTES):
                    raise ValueError("public_replay_unsafe_member")
            manifest_info = archive.getinfo("replay-manifest.json")
            if manifest_info.file_size > 128_000:
                raise ValueError("public_replay_manifest_bound")
            with archive.open(manifest_info) as source:
                manifest_bytes = source.read(128_001)
            if len(manifest_bytes) != manifest_info.file_size or _sha(manifest_bytes) != expected_root:
                raise ValueError("replay_root_mismatch")
            manifest = _document(manifest_bytes)
            if (manifest.get("format") != PUBLIC_REPLAY_FORMAT or manifest.get("audience") != "public"
                    or set(names) != allowed or set(manifest.get("members", {})) != allowed - {"replay-manifest.json"}):
                raise PermissionError("public_replay_restricted_or_unregistered_payload")
            members = {"replay-manifest.json": manifest_bytes}
            for name in sorted(allowed - {"replay-manifest.json"}):
                entry = archive.getinfo(name)
                with archive.open(entry) as source:
                    content = source.read(REPLAY_MAX_BYTES + 1)
                if len(content) != entry.file_size:
                    raise ValueError("public_replay_member_size_changed")
                members[name] = content
    return _checked_decoded_members(members, expected_root, PUBLIC_REPLAY_FORMAT)


@contextmanager
def _restricted_archive_metadata(package):
    """Inspect only the bounded ZIP index until the input partition is checked."""

    from .bundles import REPLAY_MAX_BYTES, REPLAY_MAX_EXPANDED, REPLAY_MAX_MEMBERS

    with _public_archive_stream(package) as (stream, size):
        if size < 22:
            raise ValueError("replay_archive_metadata_invalid")
        stream.seek(size - 22)
        marker, disk, directory_disk, count_disk, count, directory_bytes, offset, comment = struct.unpack("<4s4H2LH", stream.read(22))
        if (marker != b"PK\x05\x06" or disk or directory_disk or comment or count != count_disk
                or not 1 <= count <= REPLAY_MAX_MEMBERS or offset + directory_bytes != size - 22):
            raise ValueError("replay_archive_metadata_invalid")
        with zipfile.ZipFile(stream) as archive:
            entries = archive.infolist()
            if (len(entries) != count or len({entry.filename for entry in entries}) != count
                    or sum(entry.file_size for entry in entries) > REPLAY_MAX_EXPANDED):
                raise ValueError("replay_archive_inventory_bound")
            for entry in entries:
                if (not _replay_name(entry.filename) or entry.is_dir() or entry.flag_bits & 1
                        or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG)
                        or entry.file_size > REPLAY_MAX_BYTES):
                    raise ValueError("replay_unsafe_archive_member")
            yield archive, stream, {entry.filename: entry for entry in entries}


def _archive_member(archive, entries, name):
    from .bundles import REPLAY_MAX_BYTES

    entry = entries[name]
    with archive.open(entry) as source:
        raw = source.read(REPLAY_MAX_BYTES + 1)
    if len(raw) != entry.file_size:
        raise ValueError("replay_archive_size_mismatch")
    return raw


def _restricted_manifest(archive, entries, expected_root):
    raw = _archive_member(archive, entries, "replay-manifest.json")
    if _sha(raw) != expected_root:
        raise ValueError("replay_root_mismatch")
    manifest = _document(raw)
    if (manifest.get("audience") != "restricted_audit" or type(manifest.get("members")) is not dict
            or set(entries) != set(manifest["members"]) | {"replay-manifest.json"}):
        raise ValueError("replay_inventory_or_scope_mismatch")
    for name, reference in manifest["members"].items():
        if (type(reference) is not dict or set(reference) != {"sha256", "size_bytes"}
                or type(reference["sha256"]) is not str or not _SHA.fullmatch(reference["sha256"])
                or type(reference["size_bytes"]) is not int or reference["size_bytes"] != entries[name].file_size):
            raise ValueError("replay_member_binding_changed")
    return manifest, raw


def replay_package_manifest(package, expected_root, *, entitlement):
    """Dispatch metadata only. This does not decode or release object payloads."""
    entitlement = _inert_entitlement(entitlement)
    _require_replay_scope(entitlement, expected_root)
    with _restricted_archive_metadata(package) as (archive, _stream, entries):
        return _restricted_manifest(archive, entries, expected_root)[0]


def _known_input_digests(snapshot):
    """Classify only after shared metadata validation, from original source bytes."""
    if any(row["specification"]["held_out"] or row["specification"]["purpose"] == "protected_evaluation"
           for row in snapshot["steps"].values()):
        raise PermissionError("restricted_replay_excludes_protected_evaluation_lineages")
    excluded = {row["artifact"]["sha256"] for row in snapshot["artifacts"].values()
                if row["producer"] is None or row["artifact"]["kind"] == "counts"}
    sources = (_document(row["payload"].encode())
               for row in snapshot["exposures"]["original_records"].get("sources", {}).values())
    excluded.update(row["content_sha256"] for row in sources
                    if set(row["tags"]) & {"original_source_data", "protected_evaluation"})
    return excluded


def _reject_input_alias(reference, excluded, *, content_sha256=None):
    raw_reference = _reference_roles(reference) - {"frozen_literature_snapshot"}
    if content_sha256 in excluded or raw_reference and reference["sha256"] in excluded:
        raise PermissionError("investigation_original_input_alias_forbidden")


def _restricted_replay_members(package, expected_root, entitlement, format):
    """Check original input identities from retained metadata before objects."""
    entitlement = _inert_entitlement(entitlement)
    _require_replay_scope(entitlement, expected_root)
    with _restricted_archive_metadata(package) as (archive, stream, entries):
        manifest, raw_manifest = _restricted_manifest(archive, entries, expected_root)
        if manifest.get("format") != format:
            raise ValueError("replay_inventory_or_format_mismatch")
        members = {"replay-manifest.json": raw_manifest}
        def metadata(name):
            raw = _archive_member(archive, entries, name)
            if _sha(raw) != manifest["members"][name]["sha256"]:
                raise ValueError("replay_member_binding_changed")
            members[name] = raw
            return _document(raw)
        if format == INVESTIGATION_REPLAY_FORMAT:
            from ._investigation_replay import _investigation_dependencies, _validate_investigation_snapshot

            snapshot = metadata("investigation.json")
            _validate_investigation_snapshot(snapshot, manifest)
            excluded = _known_input_digests(snapshot)
            bindings, _, _ = _investigation_dependencies(snapshot)
            for reference in bindings.values():
                _reject_input_alias(reference, excluded)
            for reference in manifest["logical_bindings"].values():
                if reference.get("content_sha256") in excluded:
                    raise PermissionError("investigation_original_input_alias_forbidden")
        else:
            original = metadata("audit/manifest.json")
            if (original["root_hash"] != manifest["original_root_sha256"]
                    or _sha(_canonical({key: value for key, value in original.items() if key != "root_hash"})) != original["root_hash"]):
                raise ValueError("replay_original_root_changed")
            excluded = {row["sha256"] for row in original["artifacts"] if row["name"] == "request.json"}
            if any(reference["sha256"] in excluded for reference in manifest["members"].values()):
                raise PermissionError("investigation_original_input_alias_forbidden")
            plans = [row for row in original["artifacts"] if row["name"] == "plan.json"]
            if len(plans) != 1:
                raise ValueError("replay_original_plan_missing")
            from devharness_controlled.erql_contracts import parse_erql_plan
            plan_member = "audit/objects/" + plans[0]["sha256"]
            plan = parse_erql_plan(metadata(plan_member))
            if _sha(members[plan_member]) != plans[0]["sha256"]:
                raise ValueError("replay_original_plan_changed")
            excluded.update((plan.request_sha256, plan.source_sha256))
        if any(reference["sha256"] in excluded for reference in manifest["members"].values()):
            raise PermissionError("investigation_original_input_alias_forbidden")
        # Only the metadata-authorized partition reaches archive-byte hashing or
        # payload decompression. Current exact archive permission is rechecked.
        stream.seek(0)
        identity = hashlib.sha256()
        while chunk := stream.read(1024 * 1024):
            identity.update(chunk)
        entitlement.require(identity.hexdigest())
        for name in sorted(set(entries) - set(members)):
            members[name] = _archive_member(archive, entries, name)
    return _checked_decoded_members(members, expected_root, format), identity.hexdigest()


def public_replay_package(rendered):
    """Capture only bytes released by the current registered publication owner."""
    from .scientific_publication import RenderedPublication, _publication_renderer_sha256
    from .scientific_plot_revisions import scientific_renderer_identity
    if type(rendered) is not RenderedPublication or rendered.format != "json":
        raise TypeError("Public replay requires a trusted registered JSON publication")
    raw, reservation = rendered.release_with_receipt()
    witness = {key: reservation[key] for key in ("reservation_id", "state", "content_sha256", "content_bytes", "publication_projection")}
    witness["original_disclosure_sha256"] = _sha(_canonical(reservation))
    if witness["content_sha256"] != _sha(raw) or raw != rendered.projection_bytes:
        raise ValueError("public_replay_release_changed")
    identity = {"publication_source_sha256": _publication_renderer_sha256(), "plot_renderer": scientific_renderer_identity()}
    members = {"publication.json": raw, "release-witness.json": _canonical(witness), "renderer.json": _canonical(identity)}
    archive, receipt = _sealed_replay(members, {"format": PUBLIC_REPLAY_FORMAT, "audience": "public",
        "subject_id": rendered.subject_id, "source_sha256": _document(raw)["source_sha256"],
        "projection_sha256": _sha(raw), "scientific_acceptance": "not_granted",
        "current_use": "requires_live_owner", "raw_audit_included": False})
    return {**receipt, "bytes": archive}


def verify_public_replay_package(package, expected_root, *, current_registry=None, delivery=None):
    manifest, members = _public_replay_members(package, expected_root)
    return _verify_public_replay(manifest, members, expected_root, current_registry=current_registry, delivery=delivery)


def _verify_public_replay(manifest, members, expected_root, *, current_registry=None, delivery=None):
    if (set(manifest) != {"format", "audience", "subject_id", "source_sha256", "projection_sha256",
            "scientific_acceptance", "current_use", "raw_audit_included", "members"}
            or manifest["audience"] != "public" or manifest["raw_audit_included"] is not False
            or manifest["scientific_acceptance"] != "not_granted" or manifest["current_use"] != "requires_live_owner"
            or set(members) != {"publication.json", "release-witness.json", "renderer.json", "replay-manifest.json"}):
        raise ValueError("public_replay_scope_invalid")
    projection, witness = _document(members["publication.json"]), _document(members["release-witness.json"])
    if (projection["format"] != "devbench.adaptive-publication/1" or projection["subject_id"] != manifest["subject_id"]
            or projection["source_sha256"] != manifest["source_sha256"]
            or _sha(members["publication.json"]) != manifest["projection_sha256"]
            or witness["content_sha256"] != manifest["projection_sha256"]
            or witness["content_bytes"] != len(members["publication.json"])
            or witness["state"] != "delivery_attempted"
            or witness["publication_projection"]["subject_sha256"] != manifest["subject_id"]
            or witness["publication_projection"]["projection_sha256"] != manifest["projection_sha256"]):
        raise ValueError("public_replay_release_witness_changed")
    current = "unavailable"
    if current_registry is not None or delivery is not None:
        from .scientific_publication import PublicationRegistry
        from devbench.platform.adaptive_workflow import AdaptiveWorkflow
        from devbench.engine.workflow_operations import WorkflowOperationLedger
        if type(current_registry) is not PublicationRegistry or delivery is None:
            raise PermissionError("Current use requires the actual live publication owner and delivery")
        reader = WorkflowOperationLedger(current_registry.ledger.path, readonly=True, inert=True)
        registry = PublicationRegistry(AdaptiveWorkflow(reader, current_registry.workflow.lineage_id))
        rendered = registry.render(manifest["subject_id"], delivery=delivery, format="json")
        if rendered.content != members["publication.json"]:
            raise PermissionError("public_replay_current_publication_changed")
        current = "checked_current_projection"
    return {"verified": True, "root_sha256": expected_root, "mode": "public_evidence_verification",
            "current_use": current, "scientific_acceptance": "not_granted", "numerical_stage_launches": 0,
            "model_sessions": 0, "release_assurance": "captured_current_owner_release_not_renewed_permission"}


def replay_public_presentation(package, expected_root, output, *, current_registry=None, delivery=None):
    from devbench.engine.workspace import protected_path
    from .scientific_publication import _publication_renderer_sha256, _render_publication
    from .scientific_plot_revisions import scientific_renderer_identity
    manifest, members = _public_replay_members(package, expected_root)
    report = _verify_public_replay(manifest, members, expected_root, current_registry=current_registry, delivery=delivery)
    if _document(members["renderer.json"]) != {"publication_source_sha256": _publication_renderer_sha256(),
                                             "plot_renderer": scientific_renderer_identity()}:
        raise ValueError("public_replay_renderer_or_environment_changed")
    output = protected_path(output)
    if output.exists() or not output.parent.is_dir():
        raise ValueError("Public replay requires fresh protected presentation storage")
    projection = _document(members["publication.json"])
    rendered = {"figure.svg": _render_publication(projection, "svg")}
    result = {**report, "operation_kind": "historical_presentation", "publication_eligible": False,
              "presentation_sha256": {name: _sha(content) for name, content in rendered.items()}}
    output.mkdir(mode=0o700)
    for name, content in {**rendered, "replay-receipt.json": _canonical(result)}.items():
        with (output / name).open("xb") as stream:
            (output / name).chmod(0o600)
            stream.write(content)
    return result


def _bind_replay_use(bindings, path, identity, role, origin):
    """Deduplicate bytes, while retaining each original owner/use relationship."""
    value = bindings.setdefault(path, {"sha256": identity, "role": role, "uses": []})
    if value["sha256"] != identity:
        raise ValueError("investigation_replay_conflicting_reference_identity")
    use = {"role": role, **origin}
    if use not in value["uses"]:
        if len(value["uses"]) >= 4096:
            raise ValueError("investigation_replay_use_inventory_bound")
        value["uses"].append(use)
        value["uses"].sort(key=_canonical)
    roles = {item["role"] for item in value["uses"]}
    value["role"] = next(iter(roles)) if len(roles) == 1 else "multiple_uses"


def _reference_roles(reference):
    return ({item["role"] for item in reference["uses"]} if reference["role"] == "multiple_uses"
            else {reference["role"]})
