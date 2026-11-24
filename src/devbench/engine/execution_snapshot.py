"""Retain an allowlisted Python execution tree and launch from its bytes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import sysconfig
import tempfile
import uuid
from pathlib import Path

from devbench._layout import PROJECT_ROOT, package_root

from .workspace import protected_path, runs_root

_PACKAGES = (
    "devbench",
    "devbench_engine",
    "devharness",
    "devharness_core",
    "devharness_server",
    "devharness_controlled",
    "rna_kernels",
)
_EXCLUDED = re.compile(r"golden|grader|grading|scoring|private|secret|credential", re.I)
_RESOURCES = {
    "devbench/study/biology_catalog.json",
    "devbench/study/public_sources.json",
    "devbench/tasks/catalog.json",
    "devharness_server/r/bulk_deseq2_sensitivity.R",
    "devharness_server/r/bulk_dream.R",
    "devharness_server/r/bulk_edger.R",
    "devharness_server/r/bulk_latent_diagnostics.R",
    "devharness_server/r/bulk_voom_sensitivity.R",
    "devharness_server/r/mounted_edger.R",
    "devharness_server/r/mounted_general.R",
    "devharness_server/r/native_nb.R",
    "devharness_server/r/paired_tximport_svaseq.R",
    "devharness_server/r/rna_pathways.R",
    "devharness_server/r/scrna_emptydrops.R",
    "devharness_server/r/scrna_scran.R",
    "devbench/analysis/assets/scientific-plot-verification.json",
    "devbench/analysis/assets/plotly-strict.min.js.gz",
    "devbench/analysis/assets/plotly-strict.LICENSE.txt",
    "devbench/analysis/assets/agentic-rna-v24-evaluation-protocol.md",
    "devbench/analysis/assets/2026-11-04-workbench-compute-contracts.schema.json",
    "devbench/analysis/assets/official-client-pins.json",
    "devbench/analysis/assets/owned-host-sentinel.txt",
    "devharness_controlled/native-package-sources.lock.json",
    "devharness_controlled/native-kernel-runtime.lock.json",
    "devharness_server/r/erql_1_1.R",
    "devharness_server/r/erql_1_2.R",
    "devharness_server/r/erql_1_3.R",
    "devharness_controlled/erql-package-sources.lock.json",
}
_BUILD_FILES = (
    "pyproject.toml",
    "README.md",
    "uv.lock",
    "requirements-langgraph.lock",
    "LICENSE",
    "AGENTS.md",
    "scripts/__init__.py",
    "scripts/source_bundle.py",
    "scripts/run_erql_calibration_diagnostic.py",
)
_MANIFEST = "snapshot-manifest.json"
_BINDING = "execution-code.json"
_MAX_FILES = 2_000
_MAX_BYTES = 24_000_000
_MODULE = "devbench.analysis.episode_cli"


def _file(path, maximum):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            raise ValueError("Invalid bounded snapshot file")
        content = handle.read(maximum + 1)
        after = os.fstat(handle.fileno())
    if len(content) > maximum or _signature(before) != _signature(after):
        raise ValueError("Execution snapshot source changed during capture")
    return content, _signature(after)


def _signature(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _allowlisted(root):
    for name in _BUILD_FILES:
        if (root / name).is_file():
            yield name, root / name
    for name in _PACKAGES:
        package = package_root(root, name)
        if not package.exists():
            continue
        if package.resolve() != package or not package.is_dir():
            raise ValueError("Invalid execution snapshot package")
        for path in _package_files(package):
            relative = (Path(package.name) / path.relative_to(package)).as_posix()
            workbench_asset = path.parent == package_root(
                root, "devbench"
            ) / "analysis/assets" and path.suffix in {".css", ".html", ".js"}
            if path.suffix == ".py" or relative in _RESOURCES or workbench_asset:
                yield relative, path


def _package_files(package):
    for base, directories, files in os.walk(package, followlinks=False):
        directories[:] = sorted(
            name
            for name in directories
            if not name.startswith((".", "__")) and not _EXCLUDED.search(name)
        )
        if any((Path(base) / name).is_symlink() for name in directories):
            raise ValueError("Execution snapshot source directories cannot be symlinks")
        for name in sorted(files):
            if not name.startswith(".") and not _EXCLUDED.search(name):
                yield Path(base) / name


def _installed_build_files(project):
    """Recover only named build metadata from the installed RECORD-bound archive."""
    import io
    import zipfile
    from importlib import metadata

    resource = project / "devbench" / "analysis" / "assets" / "source-bundle.zip"
    if (project / "pyproject.toml").is_file() or not resource.is_file():
        return {}
    from scripts.source_bundle import _read_installed_resource

    payload = _read_installed_resource(resource, metadata.distribution("devharness"))
    result = {}
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for member in archive.infolist():
            if member.filename not in _BUILD_FILES:
                continue
            if member.filename in result or member.file_size > 2_000_000:
                raise ValueError("Invalid packaged build metadata")
            with archive.open(member) as handle:
                content = handle.read(2_000_001)
            if len(content) != member.file_size:
                raise ValueError("Invalid packaged build metadata size")
            result[member.filename] = content
    if not {"pyproject.toml", "README.md", "uv.lock"}.issubset(result):
        raise ValueError("Packaged source resource is missing required project files")
    return result


def materialize(project, destination):
    """Copy bounded allowlisted bytes into a freshly published snapshot."""
    project, destination = Path(project), Path(destination)
    if (
        not project.is_absolute()
        or project.resolve() != project
        or not destination.is_absolute()
        or destination.resolve() != destination
        or destination.name != "code_snapshot"
        or destination.exists()
    ):
        raise ValueError("Use a new absolute execution code_snapshot directory")
    temporary = Path(tempfile.mkdtemp(prefix=".code-", dir=destination.parent))
    signatures, manifest, total = {}, {}, 0
    try:
        for name, path in _allowlisted(project):
            if len(manifest) >= _MAX_FILES:
                raise ValueError("Execution snapshot exceeds its file limit")
            content, signatures[name] = _file(path, _MAX_BYTES - total)
            total += len(content)
            target = temporary / name
            target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            target.write_bytes(content)
            target.chmod(0o400)
            manifest[name] = hashlib.sha256(content).hexdigest()
        _retain_installed_build_files(project, temporary, manifest, total)
        observed = {
            name: _signature(path.lstat()) for name, path in _allowlisted(project)
        }
        if not manifest or signatures != observed:
            raise ValueError("Execution snapshot source changed during capture")
        raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        (temporary / _MANIFEST).write_bytes(raw)
        (temporary / _MANIFEST).chmod(0o400)
        identity = hashlib.sha256(raw).hexdigest()
        verify(temporary, identity)
        for base, _, _ in os.walk(temporary, topdown=False):
            if Path(base) != temporary:
                Path(base).chmod(0o500)
        temporary.rename(destination)
        destination.chmod(0o500)
        return identity
    finally:
        if temporary.exists():
            for base, _, _ in os.walk(temporary):
                Path(base).chmod(0o700)
            shutil.rmtree(temporary)


def _retain_installed_build_files(project, temporary, manifest, total):
    for name, content in _installed_build_files(project).items():
        if name in manifest:
            continue
        if len(manifest) >= _MAX_FILES or total + len(content) > _MAX_BYTES:
            raise ValueError("Execution snapshot exceeds its bounds")
        target = temporary / name
        target.write_bytes(content)
        target.chmod(0o400)
        manifest[name] = hashlib.sha256(content).hexdigest()
        total += len(content)


def _snapshot_members(snapshot):
    for base, directories, files in os.walk(snapshot, followlinks=False):
        if any((Path(base) / name).is_symlink() for name in directories):
            raise ValueError("Execution snapshot contains a symlink directory")
        for name in files:
            yield Path(base) / name


def verify(snapshot, identity):
    """Validate the complete inventory without accessing the live checkout."""
    snapshot = Path(snapshot)
    if not snapshot.is_absolute() or snapshot.resolve() != snapshot:
        raise ValueError("Invalid execution snapshot root")
    raw, _ = _file(snapshot / _MANIFEST, 1_000_000)
    if hashlib.sha256(raw).hexdigest() != identity:
        raise ValueError("Execution snapshot manifest changed")
    manifest = json.loads(raw)
    if not isinstance(manifest, dict) or not 1 <= len(manifest) <= _MAX_FILES:
        raise ValueError("Invalid execution snapshot inventory")
    observed, total = set(), 0
    for path in _snapshot_members(snapshot):
        relative = path.relative_to(snapshot).as_posix()
        if relative == _MANIFEST:
            continue
        if relative not in manifest:
            raise ValueError("Execution snapshot inventory changed")
        data, _ = _file(path, _MAX_BYTES - total)
        total += len(data)
        if hashlib.sha256(data).hexdigest() != manifest[relative]:
            raise ValueError("Execution snapshot content changed: " + relative)
        observed.add(relative)
    if observed != manifest.keys():
        raise ValueError("Execution snapshot inventory changed")


def snapshot_command(snapshot, identity, module, argv):
    """Start a clean application import and inherit its path into spawned workers."""
    snapshot = Path(snapshot)
    verify(snapshot, identity)
    bootstrap = """
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
root, identity, prefix, exec_prefix, site_paths, target, *arguments = sys.argv[1:]
tree = Path(root)
def read_bounded(path, maximum):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        import stat
        details = os.fstat(stream.fileno())
        if not stat.S_ISREG(details.st_mode) or details.st_size > maximum:
            raise ValueError('Invalid snapshot file before import')
        value = stream.read(maximum + 1)
        if len(value) > maximum:
            raise ValueError('Snapshot exceeds byte budget before import')
        return value
manifest = read_bounded(tree / 'snapshot-manifest.json', 1_000_000)
if hashlib.sha256(manifest).hexdigest() != identity:
    raise ValueError('Execution snapshot manifest changed before import')
expected = json.loads(manifest)
observed, size = set(), 0
for base, directories, files in os.walk(tree, followlinks=False):
    if any((Path(base) / name).is_symlink() for name in directories):
        raise ValueError('Execution snapshot has a symlink directory')
    for name in files:
        path = Path(base) / name
        relative = path.relative_to(tree).as_posix()
        if relative == 'snapshot-manifest.json':
            continue
        if relative not in expected:
            raise ValueError('Execution snapshot changed before import')
        value = read_bounded(path, 24_000_000 - size)
        size += len(value)
        if hashlib.sha256(value).hexdigest() != expected[relative]:
            raise ValueError('Execution snapshot changed before import')
        observed.add(relative)
if observed != expected.keys():
    raise ValueError('Execution snapshot inventory changed before import')
packages = (
    'devbench', 'devbench_engine', 'devharness', 'devharness_core',
    'devharness_server', 'devharness_controlled', 'rna_kernels', 'scripts',
)
if any(name.split('.')[0] in packages for name in sys.modules):
    raise RuntimeError(
        'Project modules were loaded before execution snapshot bootstrap'
    )
sys.prefix, sys.exec_prefix = prefix, exec_prefix
os.environ['DEVBENCH_EXECUTION_SNAPSHOT_ID'] = identity
sys.path.insert(0, root)
sys.path.extend(json.loads(site_paths))
for name in packages:
    if (Path(root) / name).is_dir():
        module = importlib.import_module(name)
        if not Path(module.__file__).resolve().is_relative_to(Path(root)):
            raise RuntimeError('Project import escaped execution snapshot')
module = importlib.import_module(target)
if not Path(module.__file__).resolve().is_relative_to(Path(root)):
    raise RuntimeError('Execution entry point escaped execution snapshot')
if (Path(root) / 'devbench/engine/execution_snapshot.py').is_file():
    from devbench.engine import execution_snapshot
    execution_snapshot.verify(Path(root), identity)
sys.argv = [target, *arguments]
raise SystemExit(module.main())
"""
    site_paths = sorted({sysconfig.get_path(kind) for kind in ("purelib", "platlib")})
    return [
        sys.executable,
        "-I",
        "-S",
        "-B",
        "-c",
        bootstrap,
        str(snapshot),
        identity,
        sys.prefix,
        sys.exec_prefix,
        json.dumps(site_paths),
        module,
        *argv,
    ]


def current_snapshot():
    root = Path(__file__).resolve().parents[2]
    return (
        root if root.name == "code_snapshot" and (root / _MANIFEST).is_file() else None
    )


def current_binding():
    root = current_snapshot()
    if root is None:
        return None
    identity = os.environ.get("DEVBENCH_EXECUTION_SNAPSHOT_ID", "")
    if not re.fullmatch(r"[0-9a-f]{64}", identity):
        raise ValueError("Execution snapshot is missing its admission identity")
    verify(root, identity)
    return {
        "format": "devbench.execution-code/1",
        "run_id": root.parent.name,
        "sha256": identity,
    }


def portable_snapshot(binding, root):
    """Copy exact retained source into logical members; never launch saved code."""
    from devbench.analysis.checkpoints import bounded_inventory

    if (
        type(binding) is not dict
        or set(binding) != {"format", "run_id", "sha256"}
        or binding["format"] != "devbench.execution-code/1"
        or not re.fullmatch(r"[a-f0-9]{32}", binding["run_id"])
    ):
        raise ValueError("Invalid portable execution binding")
    snapshot = Path(root) / binding["run_id"] / "code_snapshot"
    verify(snapshot, binding["sha256"])
    members = bounded_inventory(
        snapshot,
        max_files=_MAX_FILES + 1,
        max_member=_MAX_BYTES,
        max_bytes=_MAX_BYTES + 1_000_000,
    )
    verify_portable_snapshot(members, binding["sha256"])
    return members


def verify_portable_snapshot(members, identity):
    """Verify source custody in memory, independent of host run IDs and imports."""
    from devbench.study.evidence import decode_json

    raw = members[_MANIFEST]
    manifest = decode_json(raw)
    if (
        hashlib.sha256(raw).hexdigest() != identity
        or type(manifest) is not dict
        or not 1 <= len(manifest) <= _MAX_FILES
        or set(members) != set(manifest) | {_MANIFEST}
        or sum(len(raw) for name, raw in members.items() if name != _MANIFEST)
        > _MAX_BYTES
    ):
        raise ValueError("Portable execution snapshot inventory changed")
    for name, expected in manifest.items():
        if hashlib.sha256(members[name]).hexdigest() != expected:
            raise ValueError("Portable execution snapshot source changed")


def require_binding(root, expected=None):
    """Fail closed on attempted in-process continuation of a pinned episode."""
    path = root / _BINDING
    if expected is not None or path.exists() or path.is_symlink():
        content, _ = _file(path, 4096)
        binding = json.loads(content)
        if (
            expected is not None and binding != expected
        ) or binding != current_binding():
            raise ValueError("Episode requires its retained execution snapshot")


def launch(
    module,
    argv,
    *,
    episode_root=None,
    manifest=None,
    expected_root=None,
    replace_process=False,
):
    """Launch new code, or authenticate a retained binding before continuation."""
    if current_snapshot() is not None:
        return None
    runs = runs_root()
    if episode_root is not None:
        binding_path = protected_path(episode_root) / _BINDING
        if not binding_path.exists():
            return None
        manifest = protected_path(manifest)
        from .provenance import verify_execution

        raw, _ = _file(binding_path, 4096)
        checked = verify_execution(manifest, expected_root)
        if not checked["valid"]:
            raise ValueError("Episode snapshot binding receipt failed verification")
        manifest_raw, _ = _file(Path(manifest), 16_000_000)
        record = json.loads(manifest_raw)
        expected = record.pop("root_hash")
        from .provenance import canonical_json

        if (
            expected != expected_root
            or hashlib.sha256(canonical_json(record)).hexdigest() != expected_root
        ):
            raise ValueError("Episode snapshot receipt changed after verification")
        if not any(
            item.get("name") == _BINDING
            and item.get("sha256") == hashlib.sha256(raw).hexdigest()
            for item in record["artifacts"]
        ):
            raise ValueError("Episode snapshot binding is not authenticated")
        binding = json.loads(raw)
        if (
            not isinstance(binding, dict)
            or binding.get("format") != "devbench.execution-code/1"
            or not re.fullmatch(r"[0-9a-f]{32}", binding.get("run_id", ""))
            or not re.fullmatch(r"[0-9a-f]{64}", binding.get("sha256", ""))
        ):
            raise ValueError("Invalid episode snapshot binding")
        snapshot = protected_path(runs / binding["run_id"] / "code_snapshot")
        identity = binding["sha256"]
    else:
        runs.mkdir(parents=True, mode=0o700, exist_ok=True)
        allocation = protected_path(runs / uuid.uuid4().hex)
        allocation.mkdir(mode=0o700)
        snapshot = allocation / "code_snapshot"
        identity = materialize(PROJECT_ROOT, snapshot)
    environment = {
        **os.environ,
        "DEVBENCH_RUNS_ROOT": str(runs),
        "DEVBENCH_SNAPSHOT_CHECKOUT": str(PROJECT_ROOT),
    }
    command = snapshot_command(snapshot, identity, module, argv)
    if replace_process:
        os.execve(command[0], command, environment)
    return subprocess.run(command, env=environment, check=False).returncode


def launch_episode(argv, **retained):
    return launch(_MODULE, argv, **retained)
