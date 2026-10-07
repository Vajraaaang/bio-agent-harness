"""Immutable presentation children: verified plotting data, no scientific stages.

Archive import never executes saved code. Replay requires the installed renderer
to match its recorded source/runtime, then uses the existing bounded worker.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil

from .checkpoints import atomic_json, digest, sha256


def _path(value, *, exists=False):
    path = Path(value)
    if not path.is_absolute() or path != path.resolve(strict=exists):
        raise ValueError("Use absolute canonical paths without symlinks or traversal")
    return path


def _read(path, maximum=16 * 1024**2):
    from devbench.study.evidence import _read as read_bytes, decode_json
    return decode_json(read_bytes(_path(path, exists=True), maximum))


def _request(parent_root, figure_id, options, renderer, author):
    return {"parent_bundle_root": parent_root, "figure_id": figure_id,
            "display_options": options, "renderer_identity": renderer, "author_label": author}


def revision_request(parent_bundle, expected_parent_root, figure_id, display_options, *, author_label="Operator"):
    """Validate an existing parent and normalize the deduplicated request."""
    from .bundles import verify_bundle
    from .figure_artifacts import load_figure, validate_display_options, renderer_identity
    parent = _path(parent_bundle, exists=True)
    verify_bundle(parent, expected_parent_root)
    manifest = _read(parent / "receipts.json")
    if manifest.get("artifact_kind", "analysis") != "analysis":
        raise ValueError("Select the original analysis parent for a presentation revision")
    if not isinstance(author_label, str) or not author_label.strip() or len(author_label) > 200:
        raise ValueError("Use a bounded nonempty author label")
    record, payload = load_figure(parent, figure_id)
    identity = renderer_identity()
    if record["renderer_identity"] != identity:
        raise ValueError("renderer_source_or_runtime_changed: use the recorded compatible renderer")
    options = validate_display_options(display_options, record, payload)
    request = _request(expected_parent_root, figure_id, options, identity, author_label.strip())
    return {**request, "request_identity": digest(request), "parent_status": manifest["status"]}


def _parameters(parent_parameters, revision):
    return {"artifact_kind": "presentation", "execution_tier": "local_supervised",
            "data_sha256": parent_parameters["data_sha256"],
            "parent_root": revision["parent_bundle_root"],
            "render_limits": {"memory_mb": min(1024, parent_parameters.get("memory_mb", 1024)),
                              "timeout_seconds": min(120, parent_parameters.get("stage_timeout_seconds", 120))}}


def prepare_figure_revision(parent_bundle, expected_parent_root, figure_id, display_options, output, *, author_label="Operator"):
    from .figure_artifacts import load_figure
    from . import figure_artifacts
    from .runtime import observe_runtime
    from devharness_core.watchdog import _raise_if_cancelled
    parent, output = _path(parent_bundle, exists=True), _path(output)
    if output.exists() or output.is_relative_to(parent) or parent.is_relative_to(output):
        raise ValueError("Use a new output directory outside the sealed parent")
    request = revision_request(parent, expected_parent_root, figure_id, display_options, author_label=author_label)
    record, _ = load_figure(parent, figure_id)
    _raise_if_cancelled()
    output.mkdir(parents=True, mode=0o700)
    try:
        for name, target in (("receipts.json", "parent-receipts.json"),
                             ("parameters.json", "analysis-parameters.json"),
                             ("figures/manifest.json", "parent-figures.json"),
                             ("source.zip", "source.zip")):
            shutil.copyfile(_path(parent / name, exists=True), output / target)
        if (parent / "environment.lock").is_file():
            shutil.copyfile(_path(parent / "environment.lock", exists=True), output / "environment.lock")
        for item in (record["plot_data"], record["image"]):
            destination = output / item["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(_path(parent / item["path"], exists=True), destination)
        atomic_json(output / "figures/manifest.json", {"format": "devbench.figures/1", "figures": [record]})
        shutil.copyfile(Path(figure_artifacts.__file__).resolve(), output / "renderer-source.py")
        atomic_json(output / "renderer-environment.json", observe_runtime("local_supervised", detect_gpu=False))
        revision = {"format": "devbench.figure-revision/1", **request, "status": "prepared",
                    "parent_figure_data_sha256": record["plot_data"]["sha256"],
                    "analysis_binding": record["analysis_binding"], "semantics": record["semantics"]}
        atomic_json(output / "revision.json", revision)
        atomic_json(output / "parameters.json", _parameters(_read(output / "analysis-parameters.json"), revision))
        validate_presentation_bundle(output)
    except BaseException as error:
        atomic_json(output / "failure.json", {"type": type(error).__name__, "message": str(error)[:2000], "phase": "preparation"})
        raise
    return revision


def validate_presentation_bundle(root, *, completed=False):
    """Verify lineage without needing the original parent's filesystem path."""
    from .figure_artifacts import load_figure, validate_display_options
    root = _path(root, exists=True)
    revision = _read(root / "revision.json")
    if revision.get("format") != "devbench.figure-revision/1":
        raise ValueError("unknown_presentation_revision")
    if sha256(root / "parent-receipts.json") != revision["parent_bundle_root"]:
        raise ValueError("presentation_parent_root_mismatch")
    parent = _read(root / "parent-receipts.json")
    if (parent.get("format") != "devbench.analysis.bundle/1" or parent.get("artifact_kind", "analysis") != "analysis"
            or parent["status"] != revision["parent_status"]):
        raise ValueError("presentation_parent_context_mismatch")
    for original, copied in (("figures/manifest.json", "parent-figures.json"),
                             ("parameters.json", "analysis-parameters.json"), ("source.zip", "source.zip")):
        if parent["artifacts"].get(original) != sha256(root / copied):
            raise ValueError("presentation_parent_evidence_mismatch")
    originals = [row for row in _read(root / "parent-figures.json")["figures"] if row["figure_id"] == revision["figure_id"]]
    if len(originals) != 1:
        raise ValueError("presentation_parent_figure_identity")
    original = originals[0]
    record, payload = load_figure(root, revision["figure_id"])
    for key in ("analysis_binding", "semantics", "plot_data", "renderer_identity", "analysis_n", "rendered_n"):
        if record[key] != original[key]:
            raise ValueError(f"presentation_scientific_binding_changed:{key}")
    if (revision["analysis_binding"] != original["analysis_binding"] or revision["semantics"] != original["semantics"]
            or revision["parent_figure_data_sha256"] != original["plot_data"]["sha256"]
            or parent["artifacts"].get(original["plot_data"]["path"]) != original["plot_data"]["sha256"]):
        raise ValueError("presentation_scientific_evidence_changed")
    options = validate_display_options(revision["display_options"], original, payload)
    request = _request(revision["parent_bundle_root"], revision["figure_id"], options,
                       original["renderer_identity"], revision["author_label"])
    if revision["request_identity"] != digest(request) or revision["renderer_identity"] != original["renderer_identity"]:
        raise ValueError("presentation_request_changed")
    if sha256(root / "renderer-source.py") != original["renderer_identity"]["source_sha256"]:
        raise ValueError("presentation_renderer_source_changed")
    if _read(root / "parameters.json") != _parameters(_read(root / "analysis-parameters.json"), revision):
        raise ValueError("presentation_parameters_changed")
    if (root / "bindings.json").exists():
        raise ValueError("presentation_has_no_external_input_binding")
    if completed and (revision["status"] != "completed_presentation" or record["display_options"] != options):
        raise ValueError("presentation_not_completed")
    return revision


def _render_worker(root_string):
    from .figure_artifacts import load_figure, render_figure_artifact, renderer_identity
    root = _path(root_string, exists=True)
    revision = validate_presentation_bundle(root)
    if revision["renderer_identity"] != renderer_identity():
        raise ValueError("renderer_source_or_runtime_changed")
    record, payload = load_figure(root, revision["figure_id"])
    options = render_figure_artifact(record, payload, root / record["image"]["path"], revision["display_options"])
    record["display_options"] = options
    record["image"]["sha256"] = sha256(root / record["image"]["path"])
    atomic_json(root / "figures/manifest.json", {"format": "devbench.figures/1", "figures": [record]})
    return {"operation_kind": "presentation", "numerical_stage_launches": 0, "model_sessions": 0,
            "image_sha256": record["image"]["sha256"]}


def execute_figure_revision(output):
    from .bundles import seal_bundle
    from devharness_core.watchdog import run_sandboxed, _raise_if_cancelled, WorkerCancelled
    root = _path(output, exists=True)
    if any((root / name).exists() for name in ("receipts.json", "failure.json", ".execution-started")):
        raise ValueError("Revision attempt already exists, failed or sealed; it cannot silently restart")
    descriptor = os.open(root / ".execution-started", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    diagnostics = {}
    revision = None
    try:
        revision = validate_presentation_bundle(root)
        limits = _read(root / "parameters.json")["render_limits"]
        result = run_sandboxed(_render_worker, (str(root),), max_mem_mb=limits["memory_mb"],
                               timeout_sec=limits["timeout_seconds"], diagnostics=diagnostics)
        atomic_json(root / "render-execution.json", {**result, "worker": diagnostics})
        if diagnostics.get("cleanup", {}).get("verified_stopped") is not True:
            raise RuntimeError("Renderer cleanup is not verified; reconcile worker liveness before further execution")
        _raise_if_cancelled()
        revision["status"] = "completed_presentation"
        atomic_json(root / "revision.json", revision)
        receipt = seal_bundle(root, status="completed_presentation")
        return {"status": "completed_presentation", **receipt, **result}
    except BaseException as error:
        # seal_bundle publishes its root receipt last. Preserve an interrupted
        # manifest as failed-attempt evidence, never as an exportable result.
        if (root / "receipts.json").exists() and not (root / "root-receipt.json").exists():
            (root / "receipts.json").rename(root / ".unpublished-receipts.json")
        if not (root / "receipts.json").exists():
            if revision is None:
                try:
                    revision = _read(root / "revision.json")
                except (ValueError, OSError):
                    revision = None
            if revision is not None:
                revision["status"] = ("cancelled" if isinstance(error, WorkerCancelled) else
                    "recovery_required" if diagnostics.get("cleanup", {}).get("verified_stopped") is False else "operation_failed")
                atomic_json(root / "revision.json", revision)
            atomic_json(root / "failure.json", {"type": type(error).__name__, "message": str(error)[:2000],
                                                "phase": "rendering", "worker": diagnostics})
        raise


def replay_figure_revision(bundle, expected_root, output):
    from .bundles import verify_bundle
    from .figure_artifacts import renderer_identity
    source, output = _path(bundle, exists=True), _path(output)
    verify_bundle(source, expected_root)
    revision = validate_presentation_bundle(source, completed=True)
    if revision["renderer_identity"] != renderer_identity():
        raise ValueError("renderer_source_or_runtime_changed")
    if output.exists() or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("Replay requires a new directory outside the source bundle")
    manifest = _read(source / "receipts.json")
    output.mkdir(parents=True, mode=0o700)
    for name in manifest["artifacts"]:
        if name == "render-execution.json":
            continue
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_path(source / name, exists=True), target)
    revision.update(status="prepared", replay_of=expected_root)
    atomic_json(output / "revision.json", revision)
    return execute_figure_revision(output)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Replay only a verified presentation payload")
    parser.add_argument("operation", choices=["replay"])
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--expected-root", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if re.fullmatch(r"[0-9a-f]{64}", args.expected_root) is None:
        parser.error("Use a SHA-256 expected root")
    print(json.dumps(replay_figure_revision(args.bundle, args.expected_root, args.out), allow_nan=False))


if __name__ == "__main__":
    main()
