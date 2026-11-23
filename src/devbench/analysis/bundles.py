"""Legacy bundle import and the portable replay compatibility surface."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import zipfile

from ._fixed_replay import (
    REPLAY_FORMAT as REPLAY_FORMAT,
    _ERQL_ARTIFACTS as _ERQL_ARTIFACTS,
    _REPLAY_COMPARISON as _REPLAY_COMPARISON,
    _REPLAY_FIELDS as _REPLAY_FIELDS,
    _erql_replay_package as _erql_replay_package,
    _fixed_replay_projection as _fixed_replay_projection,
    _load_replay as _load_replay,
    _replay_current_use as _replay_current_use,
    _verify_replay_members as _verify_replay_members,
    describe_erql_replay as describe_erql_replay,
    export_erql_replay as export_erql_replay,
    replay_package_presentation as replay_package_presentation,
    verify_replay_package as verify_replay_package,
)
from ._investigation_replay import (
    _attempt_evidence as _attempt_evidence,
    _attempt_evidence_complete as _attempt_evidence_complete,
    _dependent_replay_references as _dependent_replay_references,
    _investigation_dependencies as _investigation_dependencies,
    _model_replay_index as _model_replay_index,
    _required_runtime_output_uses as _required_runtime_output_uses,
    _runtime_output_use_closure as _runtime_output_use_closure,
    _validate_investigation_snapshot as _validate_investigation_snapshot,
    _verify_investigation_projection as _verify_investigation_projection,
    _verify_runtime_reference as _verify_runtime_reference,
    investigation_replay_package as investigation_replay_package,
    verify_investigation_replay as verify_investigation_replay,
)
from ._replay_archive import (
    INVESTIGATION_REPLAY_FORMAT as INVESTIGATION_REPLAY_FORMAT,
    PUBLIC_REPLAY_FORMAT as PUBLIC_REPLAY_FORMAT,
    _SHA as _SHA,
    _archive_member as _archive_member,
    _audit_entitlement as _audit_entitlement,
    _bind_replay_use as _bind_replay_use,
    _canonical as _canonical,
    _checked_decoded_members as _checked_decoded_members,
    _checked_replay_members as _checked_replay_members,
    _document as _document,
    _inert_entitlement as _inert_entitlement,
    _known_input_digests as _known_input_digests,
    _public_archive_stream as _public_archive_stream,
    _public_replay_members as _public_replay_members,
    _reference_roles as _reference_roles,
    _reject_input_alias as _reject_input_alias,
    _replay_archive as _replay_archive,
    _replay_name as _replay_name,
    _require_replay_scope as _require_replay_scope,
    _restricted_archive_metadata as _restricted_archive_metadata,
    _restricted_manifest as _restricted_manifest,
    _restricted_replay_members as _restricted_replay_members,
    _sealed_replay as _sealed_replay,
    _sha as _sha,
    _verify_public_replay as _verify_public_replay,
    _zip_replay as _zip_replay,
    public_replay_package as public_replay_package,
    replay_package_manifest as replay_package_manifest,
    replay_public_presentation as replay_public_presentation,
    verify_public_replay_package as verify_public_replay_package,
)

from .checkpoints import atomic_json, sha256


REPLAY_MAX_BYTES = 16 * 1024**2
REPLAY_MAX_EXPANDED = 64 * 1024**2
REPLAY_MAX_MEMBERS = 4096
_MAX_BUNDLE_MEMBERS = 10_000
_MAX_BUNDLE_EXPANDED_BYTES = 8 * 1024**3
_MAX_BUNDLE_MEMBER_BYTES = 2 * 1024**3
_MAX_BUNDLE_RECEIPTS_BYTES = 16 * 1024**2


def _legacy_report(members, expected_root):
    from devbench.study.evidence import decode_json
    if not _SHA.fullmatch(expected_root) or hashlib.sha256(members["receipts.json"]).hexdigest() != expected_root:
        raise ValueError("bundle_root_mismatch")
    manifest = decode_json(members["receipts.json"])
    parameters = decode_json(members["parameters.json"])
    if manifest.get("format") != "devbench.analysis.bundle/1" or _kind(parameters) != _kind(manifest):
        raise ValueError("unknown_legacy_bundle_format")
    artifacts = manifest["artifacts"]
    if type(artifacts) is not dict or set(members) - {"bindings.json"} != set(artifacts) | {"receipts.json", "root-receipt.json"}:
        raise ValueError("legacy_complete_inventory_mismatch")
    for name, digest in artifacts.items():
        if not _replay_name(name) or hashlib.sha256(members[name]).hexdigest() != digest:
            raise ValueError("legacy_artifact_mismatch")
    if manifest["source_sha256"] != hashlib.sha256(members["source.zip"]).hexdigest():
        raise ValueError("legacy_source_binding_mismatch")
    receipt = decode_json(members["root-receipt.json"])
    if receipt["manifest_sha256"] != expected_root:
        raise ValueError("legacy_root_receipt_mismatch")
    return {"verified": True, "operation_kind": "legacy_readonly_import", "root_sha256": expected_root,
            "status": manifest["status"], "execution_tier": manifest["execution_tier"],
            "qualified_in_regime": False, "current_use": "unavailable", "q_scope": "historical_unmodified",
            "missing_semantics": ["lineage", "family_allocation", "current_review", "prespecification"],
            "unsupported_current_uses": ["scientific_acceptance", "publication", "numerical_execution"],
            "numerical_stage_launches": 0, "model_sessions": 0}


def verify_legacy_readonly(root, expected_root):
    """Bounded legacy inspection preserves old bytes and all original states."""
    from .checkpoints import bounded_inventory
    return _legacy_report(bounded_inventory(root), expected_root)


def import_legacy_readonly(archive, output, expected_root, *, entitlement=None):
    """Import exact legacy bytes into a new directory without adding semantics."""
    from devbench.study.evidence import _read
    from devbench.engine.workspace import protected_path
    entitlement = _audit_entitlement(entitlement)
    _require_replay_scope(entitlement, expected_root)
    raw = archive if type(archive) is bytes else _read(Path(archive), REPLAY_MAX_BYTES)
    entitlement.require(_sha(raw))
    members = _replay_archive(raw)
    report = _legacy_report(members, expected_root)
    output = protected_path(output)
    if output.exists() or not output.parent.is_dir() or "bindings.json" in members:
        raise ValueError("Legacy import requires fresh storage without external path bindings")
    entitlement.release(raw)
    output.mkdir(mode=0o700)
    for name, content in members.items():
        target = output / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with target.open("xb") as stream:
            target.chmod(0o400)
            stream.write(content)
    return report


def _kind(record):
    kind = record.get("artifact_kind", "analysis")
    if kind not in {"analysis", "presentation"}:
        raise ValueError("unknown_bundle_artifact_kind")
    return kind


def _artifact_paths(root):
    paths = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if (any(part.startswith(".") or part == "__pycache__" for part in relative.parts)
                or relative.as_posix() in {"bindings.json", "receipts.json", "root-receipt.json"}):
            continue
        if path.is_symlink():
            raise ValueError("Bundle artifacts cannot be symlinks")
        if path.is_file():
            paths.append(path)
    return sorted(paths)


def seal_bundle(root, *, status):
    root = Path(root)
    if (root/"receipts.json").exists():
        raise ValueError("A sealed bundle cannot be rewritten; use a new output for re-execution")
    parameters = json.loads((root/"parameters.json").read_text())
    kind = _kind(parameters)
    if kind == "presentation":
        from .figure_revisions import validate_presentation_bundle
        validate_presentation_bundle(root, completed=True)
    manifest = {"format": "devbench.analysis.bundle/1", "status": status,
        "execution_tier": parameters["execution_tier"], "data_sha256": parameters["data_sha256"],
        "source_sha256": sha256(root/"source.zip"), "parent_root": parameters.get("parent_root"),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "artifacts": {path.relative_to(root).as_posix(): sha256(path) for path in _artifact_paths(root)},
        "external_input": {"logical_id": "data", "binding": "bindings.json", "copied_into_bundle": False},
        "limits": "Tamper-evident against a separately retained root; no authenticated signature or compliance claim."}
    if kind == "presentation":
        manifest.update(artifact_kind="presentation", external_input=None)
    atomic_json(root/"receipts.json", manifest)
    receipt = {"format": manifest["format"], "manifest_sha256": sha256(root/"receipts.json"),
               "execution_tier": manifest["execution_tier"], "artifacts": len(manifest["artifacts"])}
    atomic_json(root/"root-receipt.json", receipt)
    return receipt


def verify_bundle(root, expected_root):
    from devbench.study.evidence import _read

    root = Path(root)
    if not root.is_absolute() or root.is_symlink():
        raise ValueError("Use a regular absolute bundle directory")
    receipts = _read(root.resolve()/"receipts.json", _MAX_BUNDLE_RECEIPTS_BYTES)
    if _sha(receipts) != expected_root:
        raise ValueError("bundle_root_mismatch")
    manifest = _document(receipts)
    if manifest.get("format") != "devbench.analysis.bundle/1":
        raise ValueError("unknown_bundle_format")
    kind = _kind(manifest)
    for name, expected in manifest["artifacts"].items():
        path = root/name
        if (PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts or "\\" in name
                or path.is_symlink() or not path.resolve().is_relative_to(root.resolve())
                or not path.is_file() or sha256(path) != expected):
            raise ValueError(f"bundle_artifact_mismatch:{name}")
    observed = {p.relative_to(root).as_posix() for p in _artifact_paths(root)}
    if observed != set(manifest["artifacts"]):
        raise ValueError("bundle_unrecorded_artifacts")
    if _kind(json.loads((root/"parameters.json").read_text())) != kind:
        raise ValueError("bundle_artifact_kind_mismatch")
    if kind == "presentation":
        if manifest.get("external_input") is not None or (root/"bindings.json").exists():
            raise ValueError("presentation_has_no_external_input_binding")
        from .figure_revisions import validate_presentation_bundle
        validate_presentation_bundle(root, completed=True)
    return {"verified": True, "root_sha256": expected_root, "artifacts": len(observed),
            "execution_tier": manifest["execution_tier"], "status": manifest["status"],
            "scope": "artifact_integrity_not_numerical_reexecution_or_biological_validation"}


def pack_bundle(root, destination, expected_root, *, audience="restricted_audit", guard=None):
    """Package exact legacy evidence for the trusted audit/replay workflow."""
    root, destination = Path(root), Path(destination)
    verify_bundle(root, expected_root)
    if audience != "restricted_audit":
        # Both legacy kinds contain exact analysis snapshots; the presentation
        # renderer has a distinct, audited public archive format.
        raise PermissionError("legacy_bundle_requires_restricted_audit")
    if guard is not None:
        manifest = json.loads((root / "receipts.json").read_text())
        guard.require(manifest["data_sha256"], manifest["source_sha256"], *manifest["artifacts"].values())
    if destination.exists() or destination.is_relative_to(root):
        raise ValueError("Use a new archive path outside the sealed bundle")
    manifest = json.loads((root/"receipts.json").read_text())
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for name in sorted([*manifest["artifacts"], "receipts.json", "root-receipt.json"]):
            archive.write(root/name, name)
    return {"archive": str(destination), "archive_sha256": sha256(destination),
            "manifest_sha256": expected_root}


def import_bundle(archive_path, output, expected_root, *, data_path=None):
    """Import without executing archive code; retain original tier and receipts."""
    output = Path(output)
    if not output.is_absolute() or output.exists():
        raise ValueError("Import requires a new absolute output directory")
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        if (len(entries) > _MAX_BUNDLE_MEMBERS or sum(entry.file_size for entry in entries) > _MAX_BUNDLE_EXPANDED_BYTES
                or any(entry.file_size > _MAX_BUNDLE_MEMBER_BYTES for entry in entries)):
            raise ValueError("bundle_archive_exceeds_limits")
        names = set()
        for entry in entries:
            name = PurePosixPath(entry.filename)
            mode = entry.external_attr >> 16
            if (name.is_absolute() or any(part in (".", "..", "") for part in entry.filename.split("/"))
                    or "\\" in entry.filename or ":" in entry.filename or entry.filename in names
                    or entry.is_dir() or stat.S_ISLNK(mode) or entry.flag_bits & 1
                    or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                    or (stat.S_IFMT(mode) not in (0, stat.S_IFREG))):
                raise ValueError("unsafe_bundle_archive_member")
            names.add(entry.filename)
        if "receipts.json" not in names:
            raise ValueError("bundle_archive_inventory_mismatch")
        # Compare the externally retained root before extracting executable source.
        receipt_entry = archive.getinfo("receipts.json")
        receipt_limit = min(_MAX_BUNDLE_RECEIPTS_BYTES, _MAX_BUNDLE_MEMBER_BYTES, _MAX_BUNDLE_EXPANDED_BYTES)
        if receipt_entry.file_size > receipt_limit:
            raise ValueError("bundle_archive_exceeds_limits")
        with archive.open(receipt_entry) as source:
            receipts = source.read(receipt_limit + 1)
        if len(receipts) > receipt_limit:
            raise ValueError("bundle_archive_exceeds_limits")
        if len(receipts) != receipt_entry.file_size:
            raise ValueError("bundle_archive_size_mismatch")
        if _sha(receipts) != expected_root:
            raise ValueError("bundle_root_mismatch")
        manifest = _document(receipts)
        if type(manifest) is not dict or manifest.get("format") != "devbench.analysis.bundle/1":
            raise ValueError("unknown_bundle_format")
        if _kind(manifest) == "presentation" and data_path is not None:
            raise ValueError("presentation_has_no_external_input_binding")
        artifacts = manifest.get("artifacts")
        if type(artifacts) is not dict or names != set(artifacts) | {"receipts.json", "root-receipt.json"}:
            raise ValueError("bundle_archive_inventory_mismatch")
        output.mkdir(mode=0o700, parents=True)
        try:
            with (output/"receipts.json").open("xb") as sink:
                sink.write(receipts)
            total = len(receipts)
            for entry in entries:
                if entry == receipt_entry:
                    continue
                destination = output/entry.filename
                destination.parent.mkdir(parents=True, exist_ok=True)
                size = 0
                with archive.open(entry) as source, destination.open("xb") as sink:
                    while chunk := source.read(min(1024**2, _MAX_BUNDLE_MEMBER_BYTES - size + 1,
                                                    _MAX_BUNDLE_EXPANDED_BYTES - total + 1)):
                        size += len(chunk)
                        total += len(chunk)
                        if size > _MAX_BUNDLE_MEMBER_BYTES or total > _MAX_BUNDLE_EXPANDED_BYTES:
                            raise ValueError("bundle_archive_exceeds_limits")
                        sink.write(chunk)
                if size != entry.file_size:
                    raise ValueError("bundle_archive_size_mismatch")
            result = verify_bundle(output, expected_root)
            if data_path is not None:
                from .runtime import verify_data_identity

                verify_data_identity(Path(data_path), manifest["data_sha256"])
                atomic_json(output/"bindings.json", {"data": str(Path(data_path).resolve())})
        except BaseException:
            shutil.rmtree(output)
            raise
    return result
