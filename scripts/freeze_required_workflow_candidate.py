"""Freeze software source, build offline, and retain actual installed byte custody.

This packager never runs scientific analyses, reviewers, jobs, models or plots.
Run snapshot/build only after source owners freeze; finalize inside the installed
candidate interpreter with -I, outside the source checkout.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
import zipfile

from devbench.analysis.workflow_candidate_freeze import (
    DEPENDENCY_FORMAT, EXECUTION_POLICY, FORMAT, LOCK_PATHS, PACKAGE_ROOTS,
    RECORD_FORMAT, SOURCE_FORMAT, _absolute, _source_map, _verify_dependency_versions, canonical_bytes,
    freeze_file_ref, validate_candidate_manifest_ref,
    installed_runtime_metadata,
)


def _write(path, value):
    path = _absolute(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(canonical_bytes(value))
    return freeze_file_ref(path)


def _branch(project):
    result = subprocess.run(["git", "-C", str(project), "symbolic-ref", "--short", "HEAD"],
                            capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout.strip()
    result = subprocess.run(["git", "-C", str(project), "rev-parse", "--verify", "HEAD"],
                            capture_output=True, text=True, check=True)
    return "detached:" + result.stdout.strip()


def source_paths(project, *, qualification=False):
    """Complete package Python sources and explicitly declared package assets."""
    paths = set()
    for name in (*LOCK_PATHS, "README.md", "LICENSE", "AGENTS.md", "MANIFEST.in"):
        path = project / name
        if path.is_file():
            paths.add(path)
    if qualification:
        paths.add(project / "colab-method-source.zip")
        paths.update((project / "qualification").iterdir())
    for name in (*PACKAGE_ROOTS, "tests"):
        root = project / name
        if root.is_dir():
            paths.update(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)
    metadata = tomllib.loads((project / "pyproject.toml").read_text())
    for package, patterns in metadata["tool"]["setuptools"].get("package-data", {}).items():
        root = project / package.replace(".", "/")
        for pattern in patterns:
            if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
                raise ValueError("Unsafe package asset pattern")
            paths.update(path for path in root.glob(pattern) if path.is_file())
    selected = []
    for path in sorted(paths):
        path = _absolute(path)
        if not path.is_relative_to(project) or any(part in {"reports", "private", ".git", ".venv", "__pycache__"} for part in path.relative_to(project).parts):
            raise ValueError("Source selection escaped its curated package closure")
        selected.append(path)
    return selected


def snapshot(project, output, qualification_assets, audit_refs):
    from devbench.analysis.workflow_protocol_qualification import QUALIFICATION_ASSET_SHA256

    project, output = _absolute(project), _absolute(output)
    if output.exists():
        raise FileExistsError(str(output))
    output.mkdir(parents=True)
    source = output / "source"
    source.mkdir()
    paths = source_paths(project)
    original = {str(path.relative_to(project)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    for path in paths:
        destination = source / path.relative_to(project)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
    asset_names = {"method.py": "method", "fixture.json": "fixture", "verifier.py": "verifier",
                   "expected_values.json": "expectations", "environment-lock.json": "environment_lock"}
    if len(qualification_assets) != 5 or {Path(path).name for path in qualification_assets} != set(asset_names):
        raise ValueError("Snapshot requires exactly the five authorized qualification asset paths")
    qualification = source / "qualification"
    qualification.mkdir()
    qualification_refs = {}
    for path in qualification_assets:
        path = _absolute(path)
        key = asset_names[path.name]
        reference = freeze_file_ref(path)
        if reference["sha256"] != QUALIFICATION_ASSET_SHA256[key]:
            raise ValueError("Qualification asset differs from the user-authorized bytes")
        destination = qualification / path.name
        shutil.copyfile(path, destination)
        qualification_refs[key] = freeze_file_ref(destination)
    retained_audits = []
    for index, path in enumerate(audit_refs):
        path = _absolute(path)
        destination = output / "merge-audit" / (str(index) + "-" + path.name)
        destination.parent.mkdir(exist_ok=True)
        shutil.copyfile(path, destination)
        retained_audits.append(freeze_file_ref(destination))
    if not retained_audits:
        raise ValueError("Snapshot requires retained merge/restriction audit records")
    # The source ZIP deliberately excludes itself. Generate from the retained
    # snapshot, not from a checkout that another writer may still be changing.
    program = ("import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); "
               "from scripts.build_source_resource import build_source_resource; "
               "from devbench.analysis.colab_job import build_method_source_bundle; "
               "print(build_source_resource(sys.argv[1])); "
               "print(build_method_source_bundle(Path(sys.argv[1]), Path(sys.argv[1])/'colab-method-source.zip'))")
    subprocess.run([sys.executable, "-I", "-B", "-c", program, str(source)], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if any(hashlib.sha256(path.read_bytes()).hexdigest() != original[str(path.relative_to(project))] for path in paths):
        raise ValueError("Source checkout changed while snapshotting")
    files = []
    for path in source_paths(source, qualification=True):
        files.append({"relative_path": path.relative_to(source).as_posix(),
                      "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size_bytes": path.stat().st_size})
    manifest = {"format": SOURCE_FORMAT, "source_root": str(source), "branch": _branch(project), "files": files,
                "qualification_assets": qualification_refs, "audit_refs": retained_audits}
    reference = _write(output / "source-manifest.json", manifest)
    _source_map(manifest, source)
    return reference


def build(source_manifest, build_python, output):
    source_manifest = _absolute(source_manifest)
    manifest = json.loads(source_manifest.read_text())
    source = _absolute(manifest["source_root"])
    _source_map(manifest, source)
    output = _absolute(output)
    if output.exists():
        raise FileExistsError(str(output))
    output.mkdir(parents=True)
    environment = dict(os.environ, SOURCE_DATE_EPOCH="315532800", PYTHONHASHSEED="0", PIP_NO_INDEX="1",
                       PYTHONDONTWRITEBYTECODE="1", PIP_NO_CACHE_DIR="1")
    if not Path(build_python).is_absolute() or not Path(build_python).is_file():
        raise ValueError("Build interpreter must be an existing absolute executable")
    result = subprocess.run([str(build_python), "-B", "-m", "pip", "wheel", "--no-cache-dir", "--no-index", "--no-deps",
                             "--no-build-isolation", "--wheel-dir", str(output), str(source)],
                            capture_output=True, text=True, env=environment)
    (output / "build.stdout.log").write_text(result.stdout)
    (output / "build.stderr.log").write_text(result.stderr)
    if result.returncode:
        raise RuntimeError("Offline candidate wheel build failed; retained build logs")
    _source_map(manifest, source)
    wheels = list(output.glob("devharness-*.whl"))
    if len(wheels) != 1:
        raise ValueError("Offline build must produce exactly one candidate wheel")
    return freeze_file_ref(wheels[0])


def finalize(source_manifest, wheel, output, test_results):
    from devbench.analysis.rna_trial_authority import verify_trial_runtime_dependencies

    source_manifest, output = _absolute(source_manifest), _absolute(output)
    source = json.loads(source_manifest.read_text())
    source_root = _absolute(source["source_root"])
    files = _source_map(source, source_root)
    runtime = Path(sys.prefix).resolve()
    if source_root == Path.cwd() or str(source_root) in sys.path:
        raise ValueError("Finalize must run outside source with an isolated installed interpreter")
    output.mkdir(parents=True, exist_ok=False)
    dependencies = verify_trial_runtime_dependencies(("devharness",), runtime_root=runtime)
    identity, external_origins = installed_runtime_metadata(dependencies)
    dependency_ref = _write(output / "dependency-lock.json", {
        "format": DEPENDENCY_FORMAT, "runtime_root": str(runtime),
        "locks": [freeze_file_ref(source_root / name) for name in LOCK_PATHS], "distributions": dependencies,
        "lock_divergences": _verify_dependency_versions(dependencies, source_root),
        "runtime_identity": identity, "external_origins": external_origins,
    })
    records = []
    for name, custody in sorted(dependencies.items()):
        distribution = importlib.metadata.distribution(name)
        paths = [item for item in distribution.files or () if str(item).endswith(".dist-info/RECORD")]
        if len(paths) != 1:
            raise ValueError("Installed dependency lacks unique RECORD")
        record = Path(distribution.locate_file(paths[0]))
        records.append(_write(output / "installed-records" / (name + ".json"), {
            "format": RECORD_FORMAT, "name": name, "version": distribution.version,
            "record_ref": freeze_file_ref(record), "metadata_ref": freeze_file_ref(record.parent / "METADATA"),
            "custody": custody,
        }))
    origins = []
    for name, row in files.items():
        if not name.endswith(".py") or name.split("/", 1)[0] not in PACKAGE_ROOTS:
            continue
        module = name[:-3].replace("/", ".").removesuffix(".__init__")
        spec = importlib.util.find_spec(module)
        if spec is None or not spec.origin:
            raise ValueError("Frozen source module lacks actual installed origin: " + module)
        origin = _absolute(spec.origin)
        if not origin.is_relative_to(runtime) or hashlib.sha256(origin.read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError("Actual installed module differs from frozen source: " + module)
        origins.append({"module": module, "path": str(origin), "sha256": row["sha256"]})
    generated = [freeze_file_ref(source_root / name) for name in
                 ("devbench/analysis/assets/source-bundle.zip", "colab-method-source.zip")]
    with zipfile.ZipFile(wheel) as archive:
        wheel_count = len({name for name in archive.namelist() if name in files and name.split("/", 1)[0] in PACKAGE_ROOTS})
    candidate = {
        "format": FORMAT, "source_manifest_ref": freeze_file_ref(source_manifest),
        "dependency_lock_ref": dependency_ref, "wheel_ref": freeze_file_ref(wheel),
        "generated_assets": generated, "installed_records": records, "module_origins": origins,
        "source_root": str(source_root), "runtime_root": str(runtime), "branch": source["branch"],
        "execution_policy": EXECUTION_POLICY,
        "closure": {"package_roots": list(PACKAGE_ROOTS), "source_file_count": len(files),
                    "wheel_source_count": wheel_count, "active_dependencies": sorted(dependencies)},
        "test_results": [freeze_file_ref(path) for path in test_results],
    }
    reference = _write(output / "candidate-manifest.json", candidate)
    validate_candidate_manifest_ref(reference, expected_sha256=reference["sha256"])
    from devbench.analysis.workflow_protocol_qualification import ProtocolQualificationProfile
    schema_ref = _write(output / "profile-schema.json", ProtocolQualificationProfile.model_json_schema())
    _write(output / "installed-verification.json", {
        "format": "devbench.workflow-candidate-installed-verification/1", "status": "verified",
        "candidate_manifest_ref": reference, "profile_schema_ref": schema_ref,
        "verification_scope": "software source/wheel/installed RECORD/import origin/schema metadata only",
        "scientific_execution": False, "scientific_qualification": False,
    })
    return reference


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("snapshot")
    stage.add_argument("--project", type=Path, required=True)
    stage.add_argument("--output", type=Path, required=True)
    stage.add_argument("--qualification-asset", type=Path, action="append", required=True)
    stage.add_argument("--audit-ref", type=Path, action="append", required=True)
    wheel = commands.add_parser("build")
    wheel.add_argument("--source-manifest", type=Path, required=True)
    wheel.add_argument("--build-python", type=Path, required=True)
    wheel.add_argument("--output", type=Path, required=True)
    final = commands.add_parser("finalize")
    final.add_argument("--source-manifest", type=Path, required=True)
    final.add_argument("--wheel", type=Path, required=True)
    final.add_argument("--output", type=Path, required=True)
    final.add_argument("--test-result", type=Path, action="append", required=True)
    args = parser.parse_args()
    if args.command == "snapshot":
        result = snapshot(args.project, args.output, args.qualification_asset, args.audit_ref)
    elif args.command == "build":
        result = build(args.source_manifest, args.build_python, args.output)
    else:
        result = finalize(args.source_manifest, args.wheel, args.output, args.test_result)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
