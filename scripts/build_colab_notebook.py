#!/usr/bin/env python3
"""Build a deterministic, self-contained Colab notebook without a Git remote.

The bundle is coordinator source. Participant staging must independently exclude
task definitions and graders; copying this whole bundle into a task is forbidden.
"""
import argparse
import base64
import hashlib
import inspect
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import zipfile


def allowed_bundle_path(name):
    """Explicit source/package allowlist; no local runtime state or user files."""
    path = PurePosixPath(name)
    if (not name or "\\" in name or ":" in name or path.is_absolute()
            or any(part in (".", "..") for part in name.split("/"))):
        return False
    denied = {".git", ".venv", ".codex", ".agents", "__pycache__", "cache", "data", "results",
              "attachments", "credentials", "secrets", "private", ".env"}
    if any(part in denied or PurePosixPath(part).stem in {"credentials", "secrets", "private"} for part in path.parts):
        return False
    if name in {"pyproject.toml", "uv.lock", "requirements-langgraph.lock", "README.md", "LICENSE", "AGENTS.md", "devbench_engine/AGENTS.md",
                "devbench/tasks/catalog.json", "benchmarks/reference_data/provenance.json",
                "benchmarks/reference_data/README.md"}:
        return True
    if name in {"devharness_server/r/native_nb.R", "devharness_controlled/native-package-sources.lock.json"}:
        return True
    if name in {"devbench/analysis/assets/workbench-ui.html",
                "devbench/analysis/assets/workbench-theme.css",
                "devbench/analysis/assets/workbench-conversation.js",
                "devbench/analysis/assets/workbench-investigations.js",
                "devbench/analysis/assets/workbench-artifacts.js",
                "devbench/analysis/assets/workbench-projects.js",
                "devbench/analysis/assets/workbench-synthetic.js",
                "devbench/analysis/assets/workbench-synthetic.css",
                "devbench/analysis/assets/workbench-demo.js",
                "devbench/analysis/assets/workbench-demo.css",
                "devbench/analysis/assets/scientific-figure.js"}:
        return True
    if name in {
        "devbench/analysis/assets/community-workflow/Dockerfile",
        "devbench/analysis/assets/community-workflow/README.md",
        "devbench/analysis/assets/community-workflow/build_local.py",
        "devbench/analysis/assets/community-workflow/collect_inventory.py",
        "devbench/analysis/assets/community-workflow/environment-explicit.linux-arm64.txt",
        "devbench/analysis/assets/community-workflow/nextflow-launcher",
        "devbench/analysis/assets/community-workflow/package-lock.linux-arm64.json",
        "devbench/analysis/assets/community-workflow/prepare_context.py",
        "devbench/analysis/assets/community-workflow/software_preflight.py",
    }:
        return True
    if path.parts[0] in {"devharness", "devharness_controlled", "devbench", "devharness_core", "devharness_server",
                         "devbench_engine", "benchmarks", "experiments"} and path.suffix == ".py":
        return True
    if name in {"tests/test_controlled_contracts.py", "tests/test_controlled_execution.py", "tests/test_controlled_verification.py", "tests/test_gaussian_inference.py",
                "tests/test_native_contracts.py", "tests/test_native_results.py", "tests/test_native_runtime.py",
                "tests/test_native_execution.py", "tests/test_native_lifecycle.py"}:
        return True
    if name in {"tests/__init__.py", "tests/test_run_registry.py", "tests/test_operated_native.py", "tests/test_operated_cli.py", "tests/test_native_recovery.py",
                "tests/test_native_effects.py", "tests/test_effect_coverage.py"}:
        return True
    if name in {"tests/test_authority_contracts.py", "tests/test_authority_store.py", "tests/test_authority_cli.py",
                "tests/test_governed_native_contracts.py", "tests/test_governed_native_execution.py",
                "tests/test_governed_dispatch_verification.py", "tests/test_native_authority_launch.py",
                "tests/test_ed25519_key_admission.py", "tests/test_sqlite_schema_guards.py", "tests/test_native_coverage_collection.py",
                "tests/test_normalization_reference.py", "tests/test_reference_native_contracts.py",
                "tests/test_mcp_protocol.py", "tests/test_temporal_evidence.py",
                "tests/test_compactor.py", "tests/test_evidence_file_types.py",
                "tests/test_selection_mc.py", "tests/test_selection_mc_api.py",
                "tests/test_bounded_artifacts.py", "tests/test_temporal_tasks.py",
                "tests/test_audit_binding.py", "tests/test_audit_cli.py", "tests/test_audit_runner.py",
                "tests/test_source_bundle_verification.py", "tests/test_runtime_identity.py",
                "tests/test_code_action_audit.py", "tests/test_code_action_audit_limits.py", "tests/test_clinical_audit_regressions.py"}:
        return True
    if name in {"scripts/__init__.py", "scripts/colab_run.py", "scripts/build_colab_notebook.py", "scripts/build_controlled_notebook.py",
                "scripts/run_live_benchmark.py", "scripts/make_leaderboard.py", "scripts/native_effect_coverage.py",
                "scripts/build_source_resource.py", "scripts/freeze_required_workflow_candidate.py"}:
        return True
    return (path.parts[:2] == ("benchmarks", "task_definitions") and path.suffix == ".json"
            and len(path.parts) in (3, 4))


def build_source_bundle(project):
    """Read only allowlisted regular source files into a bounded reproducible ZIP."""
    root = Path(project).expanduser().resolve()
    candidates = [root / name for name in ("pyproject.toml", "uv.lock", "requirements-langgraph.lock", "README.md", "LICENSE", "AGENTS.md")]
    for package in ("devharness", "devharness_controlled", "devbench", "devharness_core", "devharness_server", "devbench_engine",
                    "benchmarks", "experiments", "scripts", "tests"):
        candidates.extend((root / package).rglob("*"))
    selected = []
    total = 0
    for file in sorted(set(candidates)):
        name = file.relative_to(root).as_posix()
        if not allowed_bundle_path(name) or file.is_symlink() or not file.is_file():
            continue
        if file.resolve() != file:
            raise ValueError(f"Source bundle path resolves through a symlink: {name}")
        if file.stat().st_size > 2_000_000:
            raise ValueError(f"Source bundle file exceeds 2 MB: {name}")
        content = file.read_bytes()
        total += len(content)
        if len(content) > 2_000_000 or total > 16_000_000 or len(selected) >= 512:
            raise ValueError("Source bundle exceeds uncompressed size or file-count limit")
        selected.append((name, content))
    if not {"pyproject.toml", "README.md", "scripts/colab_run.py"}.issubset({name for name, _ in selected}):
        raise ValueError("Source bundle is missing required project files")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content in selected:
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.create_system = 3
            entry.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(entry, content, compresslevel=9)
    payload = output.getvalue()
    if len(payload) > 8_000_000:
        raise ValueError("Compressed source bundle exceeds 8 MB")
    return {"base64": base64.b64encode(payload).decode("ascii"),
            "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload),
            "uncompressed_bytes": total, "files": [name for name, _ in selected]}


def _installed_source_bundle(resource, distribution):
    """Validate a wheel's reproduction source against its installed RECORD."""
    import tempfile

    resource = Path(resource).absolute()
    files = {str(member): member for member in (distribution.files or ())}
    asset_name = "devbench/analysis/assets/source-bundle.zip"
    asset = files.get(asset_name)
    if (asset is None or asset.hash is None or asset.hash.mode != "sha256"
            or Path(distribution.locate_file(asset)).resolve() != resource.resolve()):
        raise ValueError("Packaged source resource has no matching wheel RECORD identity")
    try:
        payload = _read_source_file(resource, 8_000_000)
    except OSError as exc:
        raise ValueError("Packaged source resource is missing or unreadable") from exc
    encoded_hash = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).decode("ascii").rstrip("=")
    if encoded_hash != asset.hash.value or asset.size != len(payload):
        raise ValueError("Packaged source resource does not match wheel RECORD")
    bundle = {"base64": base64.b64encode(payload).decode("ascii"),
              "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}
    with tempfile.TemporaryDirectory(prefix="devbench-source-check-") as temporary:
        source = extract_source_bundle(bundle["base64"], Path(temporary) / "source", bundle["sha256"])
        members = sorted(path for path in source.rglob("*") if path.is_file() and path.name != ".colab_bundle.sha256")
        names = [path.relative_to(source).as_posix() for path in members]
        if not {"pyproject.toml", "README.md", "uv.lock", "scripts/colab_run.py"}.issubset(names):
            raise ValueError("Packaged source resource is missing required project files")
        # Every installed Python member must be reproduced by this exact source epoch.
        for name, member in files.items():
            if name.endswith(".py") and allowed_bundle_path(name):
                if name not in names:
                    raise ValueError("Packaged source resource omits installed module: " + name)
                expected = (source / name).read_bytes()
                installed = _read_source_file(Path(distribution.locate_file(member)), 2_000_000)
                if installed != expected:
                    raise ValueError("Packaged source resource differs from installed module: " + name)
        bundle.update(files=names, uncompressed_bytes=sum(path.stat().st_size for path in members))
    return bundle


def default_source_bundle(project=None):
    """Use an explicit checkout, or the ordinary wheel's verified source asset."""
    if project is not None:
        return build_source_bundle(project)
    root = Path(__file__).resolve().parents[1]
    if all((root / name).is_file() for name in ("pyproject.toml", "README.md", "uv.lock")):
        return build_source_bundle(root)
    from importlib import metadata, resources
    try:
        distribution = metadata.distribution("devharness")
        resource = resources.files("devbench.analysis").joinpath("assets/source-bundle.zip")
        with resources.as_file(resource) as path:
            return _installed_source_bundle(path, distribution)
    except (metadata.PackageNotFoundError, FileNotFoundError) as exc:
        raise ValueError("Packaged source resource is missing; rebuild the wheel from its frozen source epoch") from exc


def _read_source_file(path, max_bytes):
    """Read a regular source member with a byte bound and no FIFO wait."""
    if type(max_bytes) is not int or max_bytes < 0 or path.is_symlink():
        raise ValueError("Invalid source file or byte bound")
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Source must be a regular file")
        chunks, size = [], 0
        while True:
            chunk = os.read(descriptor, min(1_048_576, max_bytes - size + 1))
            if not chunk:
                return b"".join(chunks)
            size += len(chunk)
            if size > max_bytes:
                raise ValueError("Source file exceeds its byte bound")
            chunks.append(chunk)
    finally:
        os.close(descriptor)


def extract_source_bundle(encoded, destination, expected_sha256, *, verify_only=False):
    """Extract validated source, or check existing members without writing them."""
    if len(encoded) > 10_666_672:
        raise ValueError("Encoded source bundle exceeds 8 MB ZIP limit")
    payload = base64.b64decode(encoded, validate=True)
    if len(payload) > 8_000_000 or hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError("Source bundle size or SHA-256 mismatch")
    destination = Path(destination).expanduser().absolute()
    if destination.is_symlink():
        raise ValueError("Extraction destination cannot be a symlink")
    destination = destination.resolve()
    validated = []
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        entries = archive.infolist()
        if len(entries) > 512 or sum(entry.file_size for entry in entries) > 16_000_000:
            raise ValueError("Source archive exceeds file count or uncompressed byte limit")
        names = set()
        for entry in entries:
            mode = entry.external_attr >> 16
            if (not allowed_bundle_path(entry.filename) or entry.filename in names or entry.is_dir()
                    or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG))
                    or entry.flag_bits & 1 or entry.file_size > 2_000_000):
                raise ValueError("Unsafe source archive member: " + entry.filename)
            names.add(entry.filename)
            target = destination / entry.filename
            if not target.resolve().is_relative_to(destination):
                raise ValueError("Source archive member escapes destination")
            with archive.open(entry) as handle:
                content = handle.read(2_000_001)
            if len(content) != entry.file_size or len(content) > 2_000_000:
                raise ValueError("Source archive member size mismatch")
            validated.append((target, content))
    marker = destination / ".colab_bundle.sha256"
    if destination.exists() and any(destination.iterdir()):
        if (marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 65
                or _read_source_file(marker, 65).decode("ascii").strip() != expected_sha256):
            raise FileExistsError("Source directory already contains different files; choose a fresh directory")
        for target, content in validated:
            if target.is_symlink() or not target.is_file() or target.stat().st_size != len(content):
                raise ValueError("Previously extracted source changed: " + str(target.relative_to(destination)))
            if _read_source_file(target, len(content)) != content:
                raise ValueError("Previously extracted source changed: " + str(target.relative_to(destination)))
        return destination
    if verify_only:
        raise FileNotFoundError("Previously extracted source is missing or empty; verification will not recreate it")
    destination.mkdir(parents=True, exist_ok=True)
    for target, content in validated:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    marker.write_text(expected_sha256 + "\n")
    return destination


def build_notebook(project):
    """Embed the reviewed Python source; cells call its five stage functions."""
    bundle = build_source_bundle(project)
    bootstrap = """# Running this cell explicitly requests the dependency and CLI installs below.
import os
for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[name] = '1'
import base64, hashlib, importlib.util, io, json, stat, sys, zipfile
from pathlib import Path, PurePosixPath

CONTENT_ROOT = Path('/content')  # Override with an absolute directory outside Colab.
INSTALL_DEPENDENCIES = True
INSTALL_CODEX_CLI = True  # Pinned npm install only if Codex is absent; never logs in.
"""
    bootstrap += "\n" + inspect.getsource(allowed_bundle_path) + "\n" + inspect.getsource(_read_source_file) + "\n" + inspect.getsource(extract_source_bundle)
    bootstrap += f"\nSOURCE_BUNDLE_BASE64 = {bundle['base64']!r}\nSOURCE_BUNDLE_SHA256 = {bundle['sha256']!r}\n"
    bootstrap += """PROJECT = extract_source_bundle(SOURCE_BUNDLE_BASE64, CONTENT_ROOT / 'devbench_repo', SOURCE_BUNDLE_SHA256)
sys.path.insert(0, str(PROJECT))
spec = importlib.util.spec_from_file_location('devbench_colab', PROJECT / 'scripts' / 'colab_run.py')
colab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(colab)
setup = colab.setup_environment(PROJECT, install=INSTALL_DEPENDENCIES, install_codex=INSTALL_CODEX_CLI)
"""
    cells = []

    def markdown(source):
        cells.append({"cell_type": "markdown", "metadata": {}, "source": source.splitlines(keepends=True)})

    def code(source):
        cells.append({"cell_type": "code", "metadata": {}, "execution_count": None,
                      "outputs": [], "source": source.splitlines(keepends=True)})

    markdown("""# DevBench v0.4 — Codex Code-as-Action in Colab

This notebook carries its own allowlisted source ZIP; no Git remote or Mac path is needed.
It stages twelve fixed synthetic tasks and evaluates them using the shared
CodeActionAdapter coordinator. Numerical work uses one BLAS thread in fresh Python
processes; the notebook kernel only imports standard-library modules.
The source ZIP includes coordinator task/grader code, which participant staging excludes.

**Setup and login:** Running cell 1 explicitly installs the editable project with its
pyproject constraints and, when absent and npm is available, Codex CLI 0.147.0.
Start in a fresh runtime; do not independently upgrade Zarr or NumPy.
Fresh Colab requires your own **interactive ChatGPT login**. Run `!codex login --device-auth`
in a notebook cell, complete browser verification yourself when enabled for your
account, and check `!codex login status` before cell 4.
If the runtime cannot support that flow, stop and use a supported interactive runtime.
Never upload/copy Mac credentials, authentication files, or API keys to this notebook.
Setup does not log you in. Missing authentication, permission prompts, account limits,
or runtime constraints can block the run. No permission bypasses or oracle fallback are used.

Each task gets a 60-second deadline. Setup, all tasks, staging and cleanup take additional
wall time; completion under 90 seconds or absence of approval prompts is not guaranteed.
Outputs remain local to this runtime until you explicitly download/share them.

## 1. Source and explicit setup
""")
    code(bootstrap)
    markdown("## 2. Prepare twelve fixed synthetic tasks\nPreparation is not a live model evaluation. Use new directories for a new experiment.\n")
    code("preparation = colab.run_stage('prepare', CONTENT_ROOT)\n")
    markdown("""## 3. Optional processed subset
Enabled by default; set OPTIONAL_PROCESSED=False to skip network access. Reads at most 200 MB with a short timeout and retains
at most 200 genes and 60 samples. Failed or offline ingestion is labelled synthetic.
This separate exploratory data is never substituted for the fixed curated task inputs.
""")
    code("OPTIONAL_PROCESSED = True\nOPTIONAL_OFFLINE = False\ningestion = colab.run_stage('ingest', CONTENT_ROOT,\n                           optional_processed=OPTIONAL_PROCESSED, offline=OPTIONAL_OFFLINE)\n")
    markdown("## 4. Execute after interactive authentication\nThis cell makes live Codex calls when preflight succeeds. A blocker produces no fabricated task rows.\n")
    code("""MODEL = None  # None retains the Codex CLI's configured default; observed model is reported.
execution = colab.run_stage('execute', CONTENT_ROOT, model=MODEL)
""")
    markdown("""## 5. Results and local exports
The shared report prints task, track, condition, pass, time, peak MB and reason, plus
measured aggregates and failure categories. Missing telemetry remains unknown.
JSON, HTML, Markdown and SHA-256 receipts are written under `/content` by default;
download them through Colab's Files panel. Hashes describe bytes, not clinical validation,
authenticated signatures, or compliance certification. Blockers export as blocked documents.
""")
    code("report = colab.run_stage('report', CONTENT_ROOT, blocked=execution['returncode'] != 0)\n")
    return {"nbformat": 4, "nbformat_minor": 5,
            "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                         "language_info": {"name": "python"}, "colab": {"name": "colab_runner.ipynb"},
                         "devbench_source_bundle": {key: value for key, value in bundle.items() if key != "base64"}},
            "cells": [dict(cell, id=f"devbench-{index:02d}") for index, cell in enumerate(cells)]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    project = args.project.expanduser().resolve()
    output = (args.output or project / "notebooks" / "colab_runner.ipynb").expanduser().resolve()
    notebook = build_notebook(project)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(notebook, indent=1, ensure_ascii=False) + "\n")
    metadata = notebook["metadata"]["devbench_source_bundle"]
    print(json.dumps({"notebook": str(output), "bundle_bytes": metadata["bytes"], "bundle_sha256": metadata["sha256"]}))


if __name__ == "__main__":
    main()
