"""Portable artifact receipts, bounded archive import and external input binding."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
import stat
import zipfile

from .checkpoints import atomic_json, sha256


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
    root = Path(root)
    if not root.is_absolute() or root.is_symlink():
        raise ValueError("Use a regular absolute bundle directory")
    if sha256(root/"receipts.json") != expected_root:
        raise ValueError("bundle_root_mismatch")
    manifest = json.loads((root/"receipts.json").read_text())
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


def pack_bundle(root, destination, expected_root):
    root, destination = Path(root), Path(destination)
    verify_bundle(root, expected_root)
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
        if (len(entries) > 10000 or sum(e.file_size for e in entries) > 8*1024**3
                or any(e.file_size > 2*1024**3 for e in entries)):
            raise ValueError("bundle_archive_exceeds_limits")
        names = set()
        for entry in entries:
            name = PurePosixPath(entry.filename)
            mode = entry.external_attr >> 16
            if (name.is_absolute() or any(part in (".", "..", "") for part in entry.filename.split("/"))
                    or "\\" in entry.filename or ":" in entry.filename or entry.filename in names
                    or entry.is_dir() or stat.S_ISLNK(mode) or entry.flag_bits & 1
                    or (stat.S_IFMT(mode) not in (0, stat.S_IFREG))):
                raise ValueError("unsafe_bundle_archive_member")
            names.add(entry.filename)
        # Compare the externally retained root before extracting executable source.
        import hashlib
        if hashlib.sha256(archive.read("receipts.json")).hexdigest() != expected_root:
            raise ValueError("bundle_root_mismatch")
        manifest = json.loads(archive.read("receipts.json"))
        if _kind(manifest) == "presentation" and data_path is not None:
            raise ValueError("presentation_has_no_external_input_binding")
        if names != set(manifest["artifacts"]) | {"receipts.json", "root-receipt.json"}:
            raise ValueError("bundle_archive_inventory_mismatch")
        output.mkdir(parents=True)
        import shutil
        for entry in entries:
            destination = output/entry.filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as source, destination.open("xb") as sink:
                shutil.copyfileobj(source, sink, length=1024**2)
    result = verify_bundle(output, expected_root)
    if data_path is not None:
        from .runtime import verify_data_identity
        verify_data_identity(Path(data_path), manifest["data_sha256"])
        atomic_json(output/"bindings.json", {"data": str(Path(data_path).resolve())})
    return result
