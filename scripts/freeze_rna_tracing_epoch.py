"""Freeze current software sources for isolated RNA tracing verification.

This is local software preparation; it does not execute scientific workloads.
"""
import argparse
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def absolute(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Absolute non-symlink path required")
    return path


def freeze(workspace, report, epoch_name):
    workspace, report = absolute(workspace), absolute(report)
    if not report.is_relative_to(workspace) or not epoch_name.isascii() or not epoch_name.replace("-", "").isalnum():
        raise ValueError("Invalid epoch destination")
    prior = workspace / "reports/rna-agent-readiness/2026-10-04-independent-subagents-qualification"
    retained = prior / "qualified-3-source-manifest.json"
    selection_path = prior / "qualified-3-test-selection.json"
    prior_selection = json.loads(selection_path.read_bytes())
    names = set(json.loads(retained.read_bytes())["files"])
    for group in ("devbench/analysis", "devbench/study", "scripts", "tests"):
        for path in (workspace / group).glob("*rna*trac*.py"):
            names.add(str(path.relative_to(workspace)))
    for name in ("devbench/study/langsmith_outbox.py", "tests/test_langsmith_outbox.py",
                 "tests/test_rna_langsmith_export.py", "tests/test_rna_langsmith_live.py",
                 "devbench/study/rna_langsmith_export.py", "devbench/study/rna_langsmith_live.py",
                 "tests/support/rna_trace_fixture_paths.py", "tests/support/__init__.py",
                 "scripts/run_rna_tracing_verification.py", "scripts/build_source_resource.py"):
        if (workspace / name).is_file():
            names.add(name)
    # Only the already-declared bounded software selection is retained.
    broad = sorted(set(prior_selection["new_or_changed_tests"] + prior_selection["prior_96_tests"]))
    new_tests = sorted(name for name in names if name.startswith("tests/test_") and
                       ("rna_trace" in name or "rna_langsmith" in name or "langsmith_outbox" in name))
    focused = sorted(set(new_tests + ["tests/test_langsmith_export.py", "tests/test_rna_investigation_backend.py",
        "tests/test_rna_investigation_budget.py", "tests/test_rna_investigation_replay.py",
        "tests/test_langgraph_investigations.py", "tests/test_analysis_bundle.py",
        "tests/test_source_bundle_verification.py"]))
    names.update(broad + focused)
    baseline = {}
    for name in sorted(names):
        path = absolute(workspace / name)
        if not path.is_relative_to(workspace) or not path.is_file():
            raise ValueError("Missing declared software file")
        baseline[name] = sha(path)
    epoch = report / "source" / epoch_name
    epoch.mkdir(parents=True, exist_ok=False)
    for name in sorted(names):
        source, target = workspace / name, epoch / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if sha(source) != baseline[name] or sha(target) != baseline[name]:
            raise ValueError("Source changed during freeze")
    # This is source-artifact construction, before the epoch is sealed or its
    # runtime is qualified. Only the same epoch's stdlib source builder runs;
    # no scientific module, source import hook, or older runtime is borrowed.
    if "scripts/build_source_resource.py" in names:
        builder_path = epoch / "scripts/build_colab_notebook.py"
        spec = importlib.util.spec_from_file_location("_rna_epoch_source_artifact_builder", builder_path)
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        resource = builder.build_source_bundle(epoch)
        asset_name = "devbench/analysis/assets/source-bundle.zip"
        asset = epoch / asset_name
        with asset.open("xb") as stream:
            stream.write(base64.b64decode(resource["base64"], validate=True))
        baseline[asset_name] = sha(asset)
        names.add(asset_name)
        with (report / ("source-resource-" + epoch_name + ".json")).open("x") as stream:
            json.dump({"source_epoch": str(epoch), "builder": str(builder_path),
                "builder_sha256": sha(builder_path), "resource_sha256": baseline[asset_name],
                "bytes": resource["bytes"], "source_files": resource["files"],
                "qualification_performed": False}, stream, sort_keys=True, indent=2)
    manifest = epoch / "source-manifest.json"
    manifest.write_text(json.dumps({"format": "devbench.rna-tracing-source/1", "files": baseline}, sort_keys=True, indent=2) + "\n")
    selections = report / "selections" / epoch_name
    selections.mkdir(parents=True, exist_ok=False)
    for kind, tests in (("focused", focused), ("broad", sorted(set(broad + new_tests)))):
        value = {"format": "devbench.rna-tracing-selection/1", "scope": "software", "kind": kind,
            "source_manifest_sha256": sha(manifest), "prior_selection_sha256": sha(selection_path),
            "allowed_fixture_classes": ["synthetic", "local"],
            "tests": [{"path": name, "sha256": baseline[name]} for name in tests]}
        (selections / (kind + ".json")).write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    return {"epoch": str(epoch), "source_manifest_sha256": sha(manifest), "files": len(names),
            "focused_modules": len(focused), "broad_modules": len(set(broad + new_tests)), "prior_modules": len(broad)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--epoch", required=True)
    args = parser.parse_args()
    print(json.dumps(freeze(args.workspace, args.report, args.epoch), sort_keys=True))


if __name__ == "__main__":
    main()
