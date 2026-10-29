"""Frozen existing Colab bundle helpers, without algorithm changes.

Only stdlib imports are required. The job bootstrap supplies its own exact path
allowlist when embedding the bounded reader and extractor. The original broader
coordinator allowlist is retained here solely for exact source/AST provenance.
"""
import base64
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import stat
import zipfile

UPSTREAM_PATH = 'scripts/build_colab_notebook.py'
UPSTREAM_SHA256 = 'c7674b4c1fb3167db99284376e35610cecc9bb1940a1f0040cd334d4b65cf5f9'


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
    if name in {"pyproject.toml", "uv.lock", "README.md", "LICENSE", "AGENTS.md", "devbench_engine/AGENTS.md",
                "devbench/tasks/catalog.json", "benchmarks/reference_data/provenance.json",
                "benchmarks/reference_data/README.md"}:
        return True
    if name in {"devharness_server/r/native_nb.R", "devharness_controlled/native-package-sources.lock.json"}:
        return True
    if name == "devbench/analysis/assets/workbench-ui.html":
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
                "scripts/build_source_resource.py"}:
        return True
    return (path.parts[:2] == ("benchmarks", "task_definitions") and path.suffix == ".json"
            and len(path.parts) in (3, 4))


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
