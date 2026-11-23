"""Build and verify bounded, portable coordinator source archives.

Participant staging must independently exclude task definitions and graders.
"""

import base64
import hashlib
import inspect
import io
import os
import stat
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

# Room for separately packaged kernel and validation modules. Byte limits still apply.
SOURCE_BUNDLE_MAX_FILES = 768
_RETIRED_NOTEBOOK_MEMBERS = {
    "scripts/build_colab_notebook.py",
    "scripts/build_controlled_notebook.py",
    "scripts/colab_run.py",
}


def _listed_bundle_member(name):
    return name in {
        "pyproject.toml",
        "uv.lock",
        "requirements-langgraph.lock",
        "README.md",
        "LICENSE",
        "AGENTS.md",
        "devbench_engine/AGENTS.md",
        "devbench/tasks/catalog.json",
        "benchmarks/reference_data/provenance.json",
        "benchmarks/reference_data/README.md",
        "devharness_server/r/native_nb.R",
        "devharness_server/r/mounted_edger.R",
        "devharness_server/r/mounted_general.R",
        "devharness_server/r/bulk_edger.R",
        "devharness_server/r/bulk_deseq2_sensitivity.R",
        "devharness_server/r/bulk_dream.R",
        "devharness_server/r/bulk_latent_diagnostics.R",
        "devharness_server/r/erql_1_1.R",
        "devharness_server/r/erql_1_2.R",
        "devharness_server/r/erql_1_3.R",
        "devharness_controlled/native-package-sources.lock.json",
        "devharness_controlled/native-kernel-runtime.lock.json",
        "devharness_controlled/erql-package-sources.lock.json",
        "devbench/analysis/assets/workbench-ui.html",
        "devbench/analysis/assets/workbench-theme.css",
        "devbench/analysis/assets/workbench-conversation.js",
        "devbench/analysis/assets/workbench-investigations.js",
        "devbench/analysis/assets/workbench-research.js",
        "devbench/analysis/assets/workbench-studio.js",
        "devbench/analysis/assets/workbench-context.js",
        "devbench/analysis/assets/workbench-lineage.js",
        "devbench/analysis/assets/workbench-qc.js",
        "devbench/analysis/assets/workbench-thesis-studio.js",
        "devbench/analysis/assets/workbench-studio.css",
        "devbench/analysis/assets/workbench-artifacts.js",
        "devbench/analysis/assets/workbench-projects.js",
        "devbench/analysis/assets/workbench-synthetic.js",
        "devbench/analysis/assets/workbench-synthetic.css",
        "devbench/analysis/assets/workbench-demo.js",
        "devbench/analysis/assets/workbench-summary-plots.js",
        "devbench/analysis/assets/workbench-demo.css",
        "devbench/analysis/assets/scientific-figure.js",
        "devbench/analysis/assets/agentic-rna-v24-evaluation-protocol.md",
        "devbench/analysis/assets/2026-11-04-workbench-compute-contracts.schema.json",
        "devbench/analysis/assets/official-client-pins.json",
        "devbench/analysis/assets/owned-host-sentinel.txt",
        "devbench/analysis/assets/community-workflow/Dockerfile",
        "devbench/analysis/assets/community-workflow/README.md",
        "devbench/analysis/assets/community-workflow/build_local.py",
        "devbench/analysis/assets/community-workflow/collect_inventory.py",
        "devbench/analysis/assets/community-workflow/environment-explicit.linux-arm64.txt",
        "devbench/analysis/assets/community-workflow/nextflow-launcher",
        "devbench/analysis/assets/community-workflow/package-lock.linux-arm64.json",
        "devbench/analysis/assets/community-workflow/prepare_context.py",
        "devbench/analysis/assets/community-workflow/software_preflight.py",
        "tests/test_controlled_contracts.py",
        "tests/test_controlled_execution.py",
        "tests/test_controlled_verification.py",
        "tests/test_gaussian_inference.py",
        "tests/test_native_contracts.py",
        "tests/test_native_results.py",
        "tests/test_native_runtime.py",
        "tests/test_native_execution.py",
        "tests/test_native_lifecycle.py",
        "tests/__init__.py",
        "tests/test_run_registry.py",
        "tests/test_operated_native.py",
        "tests/test_operated_cli.py",
        "tests/test_native_recovery.py",
        "tests/test_native_effects.py",
        "tests/test_effect_coverage.py",
        "tests/test_authority_contracts.py",
        "tests/test_authority_store.py",
        "tests/test_authority_cli.py",
        "tests/test_governed_native_contracts.py",
        "tests/test_governed_native_execution.py",
        "tests/test_governed_dispatch_verification.py",
        "tests/test_native_authority_launch.py",
        "tests/test_ed25519_key_admission.py",
        "tests/test_sqlite_schema_guards.py",
        "tests/test_native_coverage_collection.py",
        "tests/test_normalization_reference.py",
        "tests/test_reference_native_contracts.py",
        "tests/test_mcp_protocol.py",
        "tests/test_temporal_evidence.py",
        "tests/test_compactor.py",
        "tests/test_evidence_file_types.py",
        "tests/test_selection_mc.py",
        "tests/test_selection_mc_api.py",
        "tests/test_bounded_artifacts.py",
        "tests/test_temporal_tasks.py",
        "tests/test_audit_binding.py",
        "tests/test_audit_cli.py",
        "tests/test_audit_runner.py",
        "tests/test_source_bundle_verification.py",
        "tests/test_runtime_identity.py",
        "tests/test_code_action_audit.py",
        "tests/test_code_action_audit_limits.py",
        "tests/test_clinical_audit_regressions.py",
        "scripts/__init__.py",
        "scripts/source_bundle.py",
        # Retained archive compatibility. New exports omit these runners.
        "scripts/colab_run.py",
        "scripts/build_colab_notebook.py",
        "scripts/build_controlled_notebook.py",
        "scripts/run_live_benchmark.py",
        "scripts/make_leaderboard.py",
        "scripts/native_effect_coverage.py",
        "scripts/build_source_resource.py",
        "scripts/freeze_required_workflow_candidate.py",
        "scripts/run_erql_calibration_diagnostic.py",
    }


def allowed_bundle_path(name):
    """Explicit source/package allowlist; no local runtime state or user files."""
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or ":" in name
        or path.is_absolute()
        or any(part in (".", "..") for part in name.split("/"))
    ):
        return False
    if path.parts[:2] == ("src", "devbench"):
        return allowed_bundle_path(path.relative_to("src").as_posix())
    denied = {
        ".git",
        ".venv",
        ".codex",
        ".agents",
        "__pycache__",
        "cache",
        "data",
        "results",
        "attachments",
        "credentials",
        "secrets",
        "private",
        ".env",
    }
    if any(
        part in denied
        or PurePosixPath(part).stem in {"credentials", "secrets", "private"}
        for part in path.parts
    ):
        return False
    if _listed_bundle_member(name):
        return True
    if (
        path.parts[0]
        in {
            "devharness",
            "devharness_controlled",
            "devbench",
            "devharness_core",
            "devharness_server",
            "devbench_engine",
            "rna_kernels",
            "benchmarks",
            "experiments",
        }
        and path.suffix == ".py"
    ):
        return True
    return (
        path.parts[:2] == ("benchmarks", "task_definitions")
        and path.suffix == ".json"
        and len(path.parts) in (3, 4)
    )


def _excluded_source(name):
    return any(
        word in part.lower()
        for part in PurePosixPath(name).parts
        for word in (
            "golden",
            "grader",
            "grading",
            "scoring",
            "private",
            "credential",
            "secret",
        )
    )


def checkout_source_paths(project: Path, *scopes: str) -> list[Path]:
    """Select local source membership using Git's tracked and ignore rules."""
    listing = subprocess.check_output(
        [
            "git",
            "-C",
            str(project),
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            *scopes,
        ],
    )
    return sorted({project / name for name in os.fsdecode(listing).split("\0") if name})


def _bundle_source_candidates(root):
    build_files = (
        "pyproject.toml",
        "uv.lock",
        "README.md",
        "LICENSE",
        "AGENTS.md",
    )
    devbench_path = "src/devbench" if (root / "src/devbench").is_dir() else "devbench"
    packages = (
        "devharness",
        "devharness_controlled",
        devbench_path,
        "devharness_core",
        "devharness_server",
        "devbench_engine",
        "rna_kernels",
        "benchmarks",
        "experiments",
        "scripts",
        "tests",
    )
    git_marker = root / ".git"
    if git_marker.is_file() or (git_marker / "HEAD").is_file():
        return checkout_source_paths(root, *build_files, *packages)
    candidates = [root / name for name in build_files]
    for package in packages:
        candidates.extend((root / package).rglob("*"))
    return candidates


def _read_bundle_sources(root, candidates):
    import tomllib

    try:
        layout = _read_source_file(root / "pyproject.toml", 2_000_000).decode("utf-8")
    except FileNotFoundError as error:
        raise ValueError("Source bundle is missing required project files") from error
    src_layout = (
        tomllib.loads(layout)
        .get("tool", {})
        .get("setuptools", {})
        .get("package-dir", {})
        .get("devbench")
        == "src/devbench"
    )
    selected = []
    total = 0
    for file in sorted(set(candidates)):
        name = file.relative_to(root).as_posix()
        if (
            _excluded_source(name)
            or name in _RETIRED_NOTEBOOK_MEMBERS
            or not allowed_bundle_path(name)
            or file.is_symlink()
            or not file.is_file()
        ):
            continue
        if file.resolve() != file:
            raise ValueError(f"Source bundle path resolves through a symlink: {name}")
        if file.stat().st_size > 2_000_000:
            raise ValueError(f"Source bundle file exceeds 2 MB: {name}")
        content = _read_source_file(file, 2_000_000)
        total += len(content)
        if (
            len(content) > 2_000_000
            or total > 16_000_000
            or len(selected) >= SOURCE_BUNDLE_MAX_FILES
        ):
            raise ValueError(
                "Source bundle exceeds uncompressed size or file-count limit"
            )
        if src_layout and name.startswith("devbench/"):
            name = "src/" + name
        selected.append((name, content))
    return selected, total


def build_source_bundle(project):
    """Read only allowlisted regular source files into a bounded reproducible ZIP."""
    root = Path(project).expanduser().resolve()
    selected, total = _read_bundle_sources(root, _bundle_source_candidates(root))
    if not {"pyproject.toml", "README.md", "scripts/source_bundle.py"}.issubset(
        {name for name, _ in selected}
    ):
        raise ValueError("Source bundle is missing required project files")
    output = io.BytesIO()
    with zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for name, content in selected:
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.create_system = 3
            entry.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(entry, content, compresslevel=9)
    payload = output.getvalue()
    if len(payload) > 8_000_000:
        raise ValueError("Compressed source bundle exceeds 8 MB")
    return {
        "base64": base64.b64encode(payload).decode("ascii"),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "uncompressed_bytes": total,
        "files": [name for name, _ in selected],
    }


def _read_installed_resource(resource, distribution):
    """Read a wheel reproduction archive only after checking RECORD."""
    resource = Path(resource).absolute()
    files = {str(member): member for member in (distribution.files or ())}
    asset_name = "devbench/analysis/assets/source-bundle.zip"
    asset = files.get(asset_name)
    if (
        asset is None
        or asset.hash is None
        or asset.hash.mode != "sha256"
        or Path(distribution.locate_file(asset)).resolve() != resource.resolve()
    ):
        raise ValueError(
            "Packaged source resource has no matching wheel RECORD identity"
        )
    try:
        payload = _read_source_file(resource, 8_000_000)
    except OSError as exc:
        raise ValueError("Packaged source resource is missing or unreadable") from exc
    encoded_hash = (
        base64.urlsafe_b64encode(hashlib.sha256(payload).digest())
        .decode("ascii")
        .rstrip("=")
    )
    if encoded_hash != asset.hash.value or asset.size != len(payload):
        raise ValueError("Packaged source resource does not match wheel RECORD")
    return payload


def _installed_source_bundle(resource, distribution):
    """Validate a wheel's reproduction source against its installed RECORD."""
    import tempfile

    payload = _read_installed_resource(resource, distribution)
    files = {str(member): member for member in (distribution.files or ())}
    bundle = {
        "base64": base64.b64encode(payload).decode("ascii"),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
    }
    with tempfile.TemporaryDirectory(prefix="devbench-source-check-") as temporary:
        source = extract_source_bundle(
            bundle["base64"], Path(temporary) / "source", bundle["sha256"]
        )
        members = sorted(
            path
            for path in source.rglob("*")
            if path.is_file() and path.name != ".colab_bundle.sha256"
        )
        names = [path.relative_to(source).as_posix() for path in members]
        if not {"pyproject.toml", "README.md", "uv.lock"}.issubset(names) or not {
            "scripts/source_bundle.py", "scripts/colab_run.py"
        }.intersection(names):
            raise ValueError(
                "Packaged source resource is missing required project files"
            )
        # The shipped runtime source must match the installed source epoch.
        for name, member in files.items():
            if (
                name.endswith(".py")
                and allowed_bundle_path(name)
                and not _excluded_source(name)
            ):
                source_name = (
                    "src/" + name
                    if name.startswith("devbench/") and "src/" + name in names
                    else name
                )
                if source_name not in names:
                    raise ValueError(
                        "Packaged source resource omits installed module: " + name
                    )
                expected = (source / source_name).read_bytes()
                installed = _read_source_file(
                    Path(distribution.locate_file(member)), 2_000_000
                )
                if installed != expected:
                    raise ValueError(
                        "Packaged source resource differs from installed module: "
                        + name
                    )
        bundle.update(
            files=names, uncompressed_bytes=sum(path.stat().st_size for path in members)
        )
    return bundle


def default_source_bundle(project=None):
    """Use an explicit checkout, or the ordinary wheel's verified source asset."""
    if project is not None:
        return build_source_bundle(project)
    root = Path(__file__).resolve().parents[1]
    if all(
        (root / name).is_file() for name in ("pyproject.toml", "README.md", "uv.lock")
    ):
        return build_source_bundle(root)
    from importlib import metadata, resources

    try:
        distribution = metadata.distribution("devharness")
        resource = resources.files("devbench.analysis").joinpath(
            "assets/source-bundle.zip"
        )
        with resources.as_file(resource) as path:
            return _installed_source_bundle(path, distribution)
    except (metadata.PackageNotFoundError, FileNotFoundError) as exc:
        raise ValueError(
            "Packaged source resource is missing; rebuild the wheel from its "
            "frozen source epoch"
        ) from exc


def _read_source_file(path, max_bytes):
    """Read a regular source member with a byte bound and no FIFO wait."""
    if type(max_bytes) is not int or max_bytes < 0 or path.is_symlink():
        raise ValueError("Invalid source file or byte bound")
    descriptor = os.open(
        str(path),
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
    )
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


def _validated_source_members(payload, destination):
    validated = []
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        entries = archive.infolist()
        total = sum(entry.file_size for entry in entries)
        if len(entries) > SOURCE_BUNDLE_MAX_FILES or total > 16_000_000:
            raise ValueError(
                "Source archive exceeds file count or uncompressed byte limit"
            )
        names = set()
        for entry in entries:
            mode = entry.external_attr >> 16
            if (
                not allowed_bundle_path(entry.filename)
                or entry.filename in names
                or entry.is_dir()
                or stat.S_ISLNK(mode)
                or (stat.S_IFMT(mode) not in (0, stat.S_IFREG))
                or entry.flag_bits & 1
                or entry.file_size > 2_000_000
            ):
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
    return validated


def _verify_extracted_source(marker, expected_sha256, validated, destination):
    if (
        marker.is_symlink()
        or not marker.is_file()
        or marker.stat().st_size > 65
        or _read_source_file(marker, 65).decode("ascii").strip() != expected_sha256
    ):
        raise FileExistsError(
            "Source directory already contains different files; "
            "choose a fresh directory"
        )
    for target, content in validated:
        if (
            target.is_symlink()
            or not target.is_file()
            or target.stat().st_size != len(content)
            or _read_source_file(target, len(content)) != content
        ):
            raise ValueError(
                "Previously extracted source changed: "
                + str(target.relative_to(destination))
            )


def extract_source_bundle(encoded, destination, expected_sha256, *, verify_only=False):
    """Extract validated source, or check existing members without writing them."""
    if len(encoded) > 10_666_672:
        raise ValueError("Encoded source bundle exceeds 8 MB ZIP limit")
    payload = base64.b64decode(encoded, validate=True)
    if (
        len(payload) > 8_000_000
        or hashlib.sha256(payload).hexdigest() != expected_sha256
    ):
        raise ValueError("Source bundle size or SHA-256 mismatch")
    destination = Path(destination).expanduser().absolute()
    if destination.is_symlink():
        raise ValueError("Extraction destination cannot be a symlink")
    destination = destination.resolve()
    validated = _validated_source_members(payload, destination)
    marker = destination / ".colab_bundle.sha256"
    if destination.exists() and any(destination.iterdir()):
        _verify_extracted_source(marker, expected_sha256, validated, destination)
        return destination
    if verify_only:
        raise FileNotFoundError(
            "Previously extracted source is missing or empty; verification will "
            "not recreate it"
        )
    destination.mkdir(parents=True, exist_ok=True)
    for target, content in validated:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    marker.write_text(expected_sha256 + "\n")
    return destination


def source_bundle_extractor_source():
    """Return a standalone verifier with the same limits as the local extractor."""
    helpers = (
        _listed_bundle_member,
        allowed_bundle_path,
        _read_source_file,
        _validated_source_members,
        _verify_extracted_source,
        extract_source_bundle,
    )
    return "\n".join(
        [
            f"SOURCE_BUNDLE_MAX_FILES = {SOURCE_BUNDLE_MAX_FILES}",
            *(inspect.getsource(helper) for helper in helpers),
        ]
    )
