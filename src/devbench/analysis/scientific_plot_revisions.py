"""Portable presentation children of already prepared scientific plot geometry.

Only the installed, identity-matched renderer is executed during replay. Included
code is evidence, never imported. The execution DAG is tamper-evident against an
externally retained root; it authenticates neither people nor scientific validity.
Legacy ``figure-revision/1`` remains a separate, unchanged interface.
"""
from __future__ import annotations

import hashlib
from importlib import metadata
import io
import os
from pathlib import Path
import platform
import re
import stat
import sys
import tempfile
import zipfile

from devbench.study.contracts import canonical_json_bytes
from devbench.study.evidence import _read, decode_json
from devbench.study.permissions import require_protected_storage
from devharness_core.provenance import ExecutionDAG, canonical_json, hash_artifact, verify_execution

from .figure_revisions import _path


FORMAT = "devbench.scientific-plot-revision/1"
MAX_MEMBER_BYTES = 64 * 1024**2
MAX_ARCHIVE_BYTES = 256 * 1024**2
MAX_MEMBERS = 64
_CODE = ("scientific_plot_artifacts.py", "scientific_plot_contract.py", "scientific_plot_images.py")
_PAYLOAD = {"workspace.json", "request.json", "source-binding.json", "renderer-identity.json",
            "manifest.json", "figure.svg", "figure.pdf", "figure.png", *(_CODE)}
_DIGEST = re.compile(r"[0-9a-f]{64}")
_LIMITS = "Tamper-evident against a separately retained root; no authenticated signature, scientific validation, or clinical release."


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _json(path, maximum=16_000_000):
    return decode_json(_read(path, maximum))


def _workspace_bytes(workspace):
    # A workspace is a bounded artifact, not 16 MB execution-ledger metadata.
    encoded = canonical_json_bytes(workspace)
    if len(encoded) > MAX_MEMBER_BYTES:
        raise ValueError("scientific_revision_workspace_byte_budget_exceeded")
    return encoded


def _write(path, data):
    if len(data) > MAX_MEMBER_BYTES:
        raise ValueError("scientific_revision_member_budget_exceeded")
    with path.open("xb") as sink:
        os.chmod(path, 0o600)
        sink.write(data)


def _new_output(value):
    output = require_protected_storage(_path(value))
    if output.exists() or not output.parent.is_dir():
        raise ValueError("scientific_revision_requires_new_directory_with_existing_protected_parent")
    return output


def _temporary(storage_root=None):
    root = require_protected_storage(_path(Path.cwd() if storage_root is None else storage_root))
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix=".scientific-revision-", dir=root)


def scientific_renderer_identity():
    """Record implementation and local render-runtime identity without host paths."""
    import matplotlib
    from matplotlib import ft2font
    from matplotlib.backends import _backend_agg
    from . import scientific_plot_artifacts
    directory = Path(scientific_plot_artifacts.__file__).resolve().parent
    runtime = {"python": list(sys.version_info[:3]), "platform": platform.platform(),
               "packages": {name: metadata.version(name) for name in ("matplotlib", "numpy", "pillow")},
               "backend": "Agg", "dpi": 300, "font": "DejaVu Sans",
               "binary_sha256": {"agg": _sha(_read(Path(_backend_agg.__file__).resolve(), MAX_MEMBER_BYTES)),
                                 "freetype": _sha(_read(Path(ft2font.__file__).resolve(), MAX_MEMBER_BYTES)),
                                 "font": _sha(_read(Path(matplotlib.get_data_path()).resolve() / "fonts/ttf/DejaVuSans.ttf", MAX_MEMBER_BYTES))}}
    return {"format": "devbench.scientific-plot-renderer/1", "runtime": runtime,
            "code_sha256": {name: _sha(_read(directory / name, MAX_MEMBER_BYTES)) for name in _CODE}}


def fixed_renderer_identity():
    """Bind the fixed SVG renderer to Python and its installed source closure."""
    from .scientific_publication import _publication_renderer_sha256

    return {"format": "devbench.fixed-replay-renderer/2", "python": list(sys.version_info[:3]),
            "source_sha256": _publication_renderer_sha256()}


def render_fixed_replay(publication, identity):
    """Render retained table geometry; never compute an effect or refit a model."""
    from .scientific_publication import verify_fixed_publication, fixed_csv
    if identity != fixed_renderer_identity():
        raise ValueError("fixed_replay_renderer_source_or_environment_changed")
    verify_fixed_publication(publication, publication["publication_sha256"])
    figure = publication["figure"]
    if figure is None:
        raise ValueError("fixed_replay_bound_geometry_unavailable")
    return {"figure.svg": figure["svg"].encode(), "table.csv": fixed_csv(publication)}


def _binding(workspace, workspace_sha256, parent_ref):
    required = {"source_sha256", "run_id", "artifact_id"}
    if (not isinstance(parent_ref, dict) or not required <= set(parent_ref)
            or set(parent_ref) - required - {"artifact_path", "workspace_sha256"}):
        raise ValueError("scientific_revision_parent_reference_fields_invalid")
    if (parent_ref["source_sha256"] != workspace["source"]["sha256"]
            or parent_ref["run_id"] != workspace["source"]["run_id"]
            or parent_ref.get("workspace_sha256", workspace_sha256) != workspace_sha256):
        raise ValueError("scientific_revision_parent_source_or_workspace_mismatch")
    artifact_id = parent_ref["artifact_id"]
    if artifact_id is not None and (not isinstance(artifact_id, str) or not 0 < len(artifact_id) <= 4000):
        raise ValueError("scientific_revision_parent_artifact_identity_invalid")
    source_path = _path(parent_ref["artifact_path"], exists=True) if parent_ref.get("artifact_path") is not None else None
    artifact = hash_artifact(source_path, max_bytes=MAX_MEMBER_BYTES, max_entries=1) if source_path else None
    if artifact is not None and (artifact["kind"] != "file" or artifact["sha256"] != parent_ref["source_sha256"]):
        raise ValueError("scientific_revision_parent_source_hash_mismatch")
    return {"format": "devbench.scientific-plot-source-binding/1", "source_sha256": parent_ref["source_sha256"],
            "run_id": parent_ref["run_id"], "artifact_id": artifact_id, "workspace_sha256": workspace_sha256,
            "source_artifact": artifact,
            "verification": "local_file_hash_checked" if artifact is not None else "caller_asserted_source_reference",
            "scope": "Source custody and exact saved workspace only; no independent scientific validation."}, source_path


def _portable_receipt(root_hash, manifest):
    return {"format": FORMAT, "root_hash": root_hash, "manifest_path": "evidence/manifest.json",
            "status": "completed_presentation", "operation_kind": "presentation",
            "numerical_stage_launches": 0, "model_sessions": 0,
            "workspace_sha256": manifest["artifacts"]["workspace.json"],
            "request_sha256": manifest["artifacts"]["request.json"],
            "source_sha256": manifest["source_sha256"], "limits": _LIMITS}


def _create(output, workspace, request, binding, *, source_path=None, replay_of=None):
    from . import scientific_plot_artifacts
    if workspace["source"].get("publication_scope") == "registered_descriptive_presentation":
        from .bulk_plot_service import audit_public_scientific_workspace
        audit_public_scientific_workspace(workspace, request, current_use=False)
    workspace_bytes, request_bytes = _workspace_bytes(workspace), canonical_json(request)
    scientific_plot_artifacts.export_coverage(workspace, request)  # The renderer's presentation-only allowlist.
    output = _new_output(output)
    if source_path and (output.is_relative_to(source_path.parent) or source_path.parent.is_relative_to(output)):
        raise ValueError("scientific_revision_output_overlaps_parent_source")
    identity = scientific_renderer_identity()
    output.mkdir(mode=0o700)
    payload = output / "payload"
    payload.mkdir(mode=0o700)
    try:
        for name, value in (("workspace.json", workspace_bytes), ("request.json", request_bytes),
                            ("source-binding.json", canonical_json(binding)), ("renderer-identity.json", canonical_json(identity))):
            _write(payload / name, value)
        directory = Path(scientific_plot_artifacts.__file__).resolve().parent
        for name in _CODE:
            _write(payload / name, _read(directory / name, MAX_MEMBER_BYTES))
            if _sha(_read(payload / name, MAX_MEMBER_BYTES)) != identity["code_sha256"][name]:
                raise ValueError("scientific_revision_renderer_changed")
        saved_workspace, saved_request = _json(payload / "workspace.json", MAX_MEMBER_BYTES), _json(payload / "request.json")
        for suffix in ("svg", "pdf", "png"):
            _write(payload / ("figure." + suffix), scientific_plot_artifacts.render_scientific_plot(saved_workspace, saved_request, format=suffix))
        if _workspace_bytes(workspace) != workspace_bytes or canonical_json(request) != request_bytes:
            raise ValueError("scientific_revision_parent_workspace_or_request_changed")
        if _workspace_bytes(saved_workspace) != workspace_bytes or canonical_json(saved_request) != request_bytes:
            raise ValueError("scientific_revision_renderer_mutated_saved_geometry")
        if source_path and hash_artifact(source_path, max_bytes=MAX_MEMBER_BYTES, max_entries=1) != binding["source_artifact"]:
            raise ValueError("scientific_revision_parent_source_changed")
        if scientific_renderer_identity() != identity:
            raise ValueError("scientific_revision_renderer_changed")
        manifest = {"format": FORMAT, "status": "completed_presentation", "operation_kind": "presentation",
                    "source_sha256": binding["source_sha256"], "replay_of": replay_of,
                    "numerical_stage_launches": 0, "model_sessions": 0, "limits": _LIMITS,
                    "artifacts": {name: _sha(_read(payload / name, MAX_MEMBER_BYTES)) for name in sorted(_PAYLOAD - {"manifest.json"})}}
        _write(payload / "manifest.json", canonical_json(manifest))
        dag = ExecutionDAG(output / "evidence")
        artifact = dag.add_artifact(payload, max_bytes=MAX_ARCHIVE_BYTES // 2, max_entries=len(_PAYLOAD))
        dag.append("scientific_plot_presentation", {"operation_kind": "presentation", "payload": artifact,
                   "numerical_stage_launches": 0, "model_sessions": 0, "replay_of": replay_of})
        sealed = dag.finalize()
        if source_path and hash_artifact(source_path, max_bytes=MAX_MEMBER_BYTES, max_entries=1) != binding["source_artifact"]:
            raise ValueError("scientific_revision_parent_source_changed")
        receipt = _portable_receipt(sealed["root_hash"], manifest)
        _write(output / "root-receipt.json", canonical_json(receipt))
        _verify_directory(output, receipt["root_hash"])
        return receipt
    except BaseException as error:
        # A failed attempt is retained and cannot be reused as a completed child.
        (output / "root-receipt.json").unlink(missing_ok=True)
        _write(output / "failure.json", canonical_json({"status": "operation_failed", "type": type(error).__name__, "message": str(error)[:2000]}))
        raise


def create_scientific_plot_revision(output, workspace, request, *, parent_ref, publication_guard=None):
    """Create a fresh protected child; trusted parent paths never come from request.

    ``parent_ref`` requires source_sha256, run_id and a nullable artifact_id.
    Optional artifact_path is hashed before and after rendering, then omitted from
    the portable record. Without it, source custody remains a caller assertion.
    """
    from .scientific_plot_artifacts import export_coverage
    export_coverage(workspace, request)
    if workspace["source"].get("publication_scope") == "registered_descriptive_presentation":
        from .bulk_plot_service import audit_public_scientific_workspace
        audit_public_scientific_workspace(workspace, request, guard=publication_guard)
    binding, source_path = _binding(workspace, _sha(_workspace_bytes(workspace)), parent_ref)
    return _create(output, workspace, request, binding, source_path=source_path)


def _allowed_member(name):
    return (name in {"root-receipt.json", "evidence/ledger.json", "evidence/manifest.json", "evidence/root.sha256"}
            or name in {"payload/" + value for value in _PAYLOAD}
            or re.fullmatch(r"evidence/objects/[0-9a-f]{64}", name) is not None
            or re.fullmatch(r"evidence/nodes/[0-9a-f]{64}\.json", name) is not None)


def _audit_public_payload(root, *, guard=None, current_use=True):
    """Inspect actual archive payload, including the bytes duplicated by the DAG."""
    from .bulk_plot_service import audit_public_scientific_workspace

    workspace = _json(root / "payload/workspace.json", MAX_MEMBER_BYTES)
    request = _json(root / "payload/request.json")
    audit_public_scientific_workspace(workspace, request, guard=guard, current_use=current_use)
    binding = _json(root / "payload/source-binding.json")
    if binding["verification"] != "local_file_hash_checked":
        raise PermissionError("publication_plot_requires_verified_parent")
    inventory = _inventory(root)
    # The exact source may be hashed for lineage but cannot be embedded
    # in either the payload or the content-addressed object inventory.
    identities = {_sha(_read(root / name, MAX_MEMBER_BYTES)) for name in inventory}
    if (binding["source_sha256"] in identities
            or any(name.rsplit("/", 1)[-1] == binding["source_sha256"] for name in inventory)):
        raise PermissionError("publication_archive_contains_original_source")
    if guard is not None:
        guard.require(binding["source_sha256"], *identities)



def _inventory(root):
    files, total = {}, 0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("scientific_revision_symlink_forbidden")
        if path.is_dir():
            if path.relative_to(root).as_posix() not in {"payload", "evidence", "evidence/nodes", "evidence/objects"}:
                raise ValueError("scientific_revision_unknown_directory")
            continue
        name = path.relative_to(root).as_posix()
        if not _allowed_member(name) or not stat.S_ISREG(path.stat().st_mode):
            raise ValueError("scientific_revision_unknown_or_nonregular_member")
        size = path.stat().st_size
        files[name] = size
        total += size
        if len(files) > MAX_MEMBERS or size > MAX_MEMBER_BYTES or total > MAX_ARCHIVE_BYTES:
            raise ValueError("scientific_revision_archive_budget_exceeded")
    return files


def _verify_directory(root, expected_root):
    root = _path(root, exists=True)
    if not isinstance(expected_root, str) or not _DIGEST.fullmatch(expected_root):
        raise ValueError("scientific_revision_requires_external_expected_root")
    inventory = _inventory(root)
    report = verify_execution(root / "evidence/manifest.json", expected_root)
    if not report["valid"]:
        raise ValueError("scientific_revision_evidence_invalid: " + "; ".join(report["errors"]))
    evidence = _json(root / "evidence/manifest.json")
    actual = hash_artifact(root / "payload", max_bytes=MAX_ARCHIVE_BYTES // 2, max_entries=len(_PAYLOAD))
    if evidence["artifacts"] != [actual] or evidence["node_count"] != 1:
        raise ValueError("scientific_revision_payload_mismatch")
    node_id = evidence["node_ids"][0]
    node = _json(root / "evidence/nodes" / (node_id + ".json"))
    for name in ("manifest.json", "ledger.json", "nodes/" + node_id + ".json"):
        path = root / "evidence" / name
        if _read(path, 16_000_000) != canonical_json(_json(path)):
            raise ValueError("scientific_revision_noncanonical_evidence_changed")
    manifest = _json(root / "payload/manifest.json")
    if (set(manifest) != {"format", "status", "operation_kind", "source_sha256", "replay_of", "numerical_stage_launches", "model_sessions", "limits", "artifacts"}
            or manifest["format"] != FORMAT or manifest["status"] != "completed_presentation"
            or manifest["operation_kind"] != "presentation" or manifest["numerical_stage_launches"] != 0
            or manifest["model_sessions"] != 0 or manifest["limits"] != _LIMITS
            or set(manifest["artifacts"]) != _PAYLOAD - {"manifest.json"}
            or manifest["replay_of"] is not None and not _DIGEST.fullmatch(manifest["replay_of"])):
        raise ValueError("scientific_revision_manifest_invalid")
    expected_node = {"operation_kind": "presentation", "payload": actual, "numerical_stage_launches": 0,
                     "model_sessions": 0, "replay_of": manifest["replay_of"]}
    if node["kind"] != "scientific_plot_presentation" or node["payload"] != expected_node:
        raise ValueError("scientific_revision_execution_scope_mismatch")
    objects = {actual["sha256"], *(entry["sha256"] for entry in actual["entries"])}
    expected_names = {"root-receipt.json", "evidence/manifest.json", "evidence/ledger.json", "evidence/root.sha256",
                      "evidence/nodes/" + node_id + ".json", *("evidence/objects/" + value for value in objects),
                      *("payload/" + value for value in _PAYLOAD)}
    if set(inventory) != expected_names:
        raise ValueError("scientific_revision_inventory_mismatch")
    for name, digest in manifest["artifacts"].items():
        if _sha(_read(root / "payload" / name, MAX_MEMBER_BYTES)) != digest:
            raise ValueError("scientific_revision_artifact_mismatch:" + name)
    receipt = _portable_receipt(expected_root, manifest)
    if _read(root / "root-receipt.json", 16_000_000) != canonical_json(receipt):
        raise ValueError("scientific_revision_root_receipt_mismatch")
    workspace, request = _json(root / "payload/workspace.json", MAX_MEMBER_BYTES), _json(root / "payload/request.json")
    from .scientific_plot_artifacts import export_coverage
    export_coverage(workspace, request)
    binding = _json(root / "payload/source-binding.json")
    if (set(binding) != {"format", "source_sha256", "run_id", "artifact_id", "workspace_sha256", "source_artifact", "verification", "scope"}
            or binding["format"] != "devbench.scientific-plot-source-binding/1"
            or binding["source_sha256"] != workspace["source"]["sha256"] or binding["source_sha256"] != manifest["source_sha256"]
            or binding["workspace_sha256"] != manifest["artifacts"]["workspace.json"] or binding["run_id"] != workspace["source"]["run_id"]):
        raise ValueError("scientific_revision_source_binding_mismatch")
    artifact = binding["source_artifact"]
    if artifact is None:
        if binding["verification"] != "caller_asserted_source_reference":
            raise ValueError("scientific_revision_source_assurance_mismatch")
    elif (binding["verification"] != "local_file_hash_checked" or artifact.get("kind") != "file"
          or artifact.get("sha256") != binding["source_sha256"]):
        raise ValueError("scientific_revision_source_artifact_mismatch")
    identity = _json(root / "payload/renderer-identity.json")
    if (identity.get("format") != "devbench.scientific-plot-renderer/1"
            or identity.get("code_sha256") != {name: manifest["artifacts"][name] for name in _CODE}):
        raise ValueError("scientific_revision_renderer_binding_mismatch")
    if workspace["source"].get("publication_scope") == "registered_descriptive_presentation":
        _audit_public_payload(root, current_use=False)
    return {"verified": True, "root_hash": expected_root, "receipt": receipt, "manifest": manifest,
            "workspace": workspace, "request": request, "source_binding": binding, "renderer_identity": identity}


def _extract(raw, output):
    if not isinstance(raw, bytes) or len(raw) > MAX_ARCHIVE_BYTES:
        raise ValueError("scientific_revision_archive_bytes_required_or_budget_exceeded")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(entries) > MAX_MEMBERS or len(set(names)) != len(names) or sum(e.file_size for e in entries) > MAX_ARCHIVE_BYTES:
            raise ValueError("scientific_revision_archive_budget_or_duplicates")
        for entry in entries:
            mode = entry.external_attr >> 16
            if (not _allowed_member(entry.filename) or entry.file_size > MAX_MEMBER_BYTES or entry.flag_bits & 1
                    or entry.is_dir() or stat.S_IFMT(mode) not in (0, stat.S_IFREG)):
                raise ValueError("scientific_revision_unsafe_archive_member")
        output.mkdir(mode=0o700)
        for entry in entries:
            target = output / entry.filename
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with archive.open(entry) as source:
                content = source.read(MAX_MEMBER_BYTES + 1)
            _write(target, content)


def load_scientific_revision_bundle(bundle, expected_root, *, storage_root=None):
    """Verify a directory or bounded ZIP bytes against a separately retained root."""
    if isinstance(bundle, bytes):
        with _temporary(storage_root) as temporary:
            root = Path(temporary) / "imported"
            _extract(bundle, root)
            return _verify_directory(root, expected_root)
    return _verify_directory(bundle, expected_root)


def export_scientific_revision_bundle(workspace, request, *, parent_ref, storage_root=None, publication_guard=None):
    """Return {bytes, root_hash, receipt}; retain root_hash outside the ZIP."""
    with _temporary(storage_root) as temporary:
        root = Path(temporary) / "revision"
        if publication_guard is not None:
            from .bulk_plot_service import audit_public_scientific_workspace
            audit_public_scientific_workspace(workspace, request, guard=publication_guard)
        receipt = create_scientific_plot_revision(root, workspace, request, parent_ref=parent_ref,
                                                  publication_guard=publication_guard)
        if publication_guard is not None:
            _audit_public_payload(root, guard=publication_guard)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for name in sorted(_inventory(root)):
                archive.writestr(name, _read(root / name, MAX_MEMBER_BYTES))
        raw = output.getvalue()
        if workspace["source"].get("publication_scope") == "registered_descriptive_presentation":
            # Read the returned bytes through the same archive and evidence
            # checks. Directory validation alone would not bind the ZIP members.
            archived = Path(temporary) / "archived"
            _extract(raw, archived)
            _verify_directory(archived, receipt["root_hash"])
            if publication_guard is not None:
                _audit_public_payload(archived, guard=publication_guard)
        return {"bytes": raw, "root_hash": receipt["root_hash"], "receipt": receipt}


def replay_scientific_plot_revision(bundle, expected_root, output, *, publication_guard=None):
    """Render exact saved geometry again; never refit, normalize or execute saved code."""
    output = _new_output(output)
    if not isinstance(bundle, bytes):
        source = _path(bundle, exists=True)
        if source.is_relative_to(output) or output.is_relative_to(source):
            raise ValueError("scientific_revision_replay_output_overlaps_source")
    loaded = load_scientific_revision_bundle(bundle, expected_root, storage_root=output.parent)
    if publication_guard is not None:
        from .bulk_plot_service import audit_public_scientific_workspace
        audit_public_scientific_workspace(loaded["workspace"], loaded["request"], guard=publication_guard)
    if loaded["renderer_identity"] != scientific_renderer_identity():
        raise ValueError("scientific_revision_renderer_source_or_runtime_changed")
    return _create(output, loaded["workspace"], loaded["request"], loaded["source_binding"], replay_of=expected_root)
