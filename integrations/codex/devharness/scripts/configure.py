"""Create a machine-specific local DevHarness plugin copy."""

import argparse
import fcntl
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_SQLITE_LIBS = (
    Path("/opt/homebrew/opt/sqlite/lib"),
    Path("/usr/local/opt/sqlite/lib"),
)
SKILL_NAMES = (
    "rna-research-core",
    "scrna-snrna-qc-integration",
    "pseudobulk-and-de",
    "composition-and-da",
    "trajectory-and-programs",
    "spatial-and-ccc",
    "method-benchmarking-and-critic",
    "private-data-studio",
)
# A configured copy contains its own snapshot and can be used independently.
SKILL_ROOT = (
    PLUGIN_ROOT.parents[2] / ".agents" / "skills"
    if PLUGIN_ROOT.parts[-3:] == ("integrations", "codex", "devharness")
    else PLUGIN_ROOT / "skills"
)
MIRROR_MANIFEST = ".devharness-mirror-manifest.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
THREADS = {
    key: "1"
    for key in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "NUMBA_NUM_THREADS",
    )
}
_QUERY = """
import importlib.metadata as metadata
import importlib.util
import json
import sqlite3
import sys
import sysconfig

if sys.version_info < (3, 13):
    raise SystemExit("Python 3.13+ is required")
from devbench.engine.scientific_authority import require_patched_wal
from devharness_controlled.native_contracts import (
    limma_voom_kernel_images,
    routed_de_kernel_images,
)
from devbench.engine.workflow_operations import OperationConflict
standalone = sysconfig.get_config_var("PYTHON_BUILD_STANDALONE")
if standalone and sqlite3.sqlite_version_info < (3, 51, 3):
    raise SystemExit("Managed Python requires SQLite >= 3.51.3")
try:
    require_patched_wal()
except OperationConflict as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(1 if standalone else 78)
distribution = metadata.distribution("devharness")
top_level = distribution.read_text("top_level.txt")
packages = top_level.splitlines() if top_level else [
    name for name, owners in metadata.packages_distributions().items()
    if "devharness" in owners
]
origins = [
    spec.origin for name in packages
    if name.startswith(("devbench", "devharness", "rna_kernels"))
    and (spec := importlib.util.find_spec(name)) is not None and spec.origin
]
if not origins:
    raise SystemExit("Installed DevHarness package origins are unavailable")
print(json.dumps({
    "backend_version": distribution.version,
    "mcp_version": metadata.version("mcp"),
    "backend_origins": origins,
    "python_version": sys.version.split()[0],
    "sqlite_version": sqlite3.sqlite_version,
    "pinned_images": sorted(limma_voom_kernel_images()),
    "preferred_images": sorted(routed_de_kernel_images()),
}))
"""


def _absolute(value, *, executable=False):
    path = Path(value).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Use a canonical absolute path")
    # A venv executable can be a symlink to the base runtime.
    if not executable and path != path.resolve():
        raise ValueError("Use canonical paths without symlinks")
    return path


def _overlap(first, second):
    return first.is_relative_to(second) or second.is_relative_to(first)


def _fallback_libraries():
    if sys.platform == "darwin":
        paths = _SQLITE_LIBS
        if platform.machine().lower() == "x86_64":
            paths = tuple(reversed(paths))
        yield from (("DYLD_LIBRARY_PATH", lib) for lib in paths if lib.is_dir())
    elif sys.platform.startswith("linux") and os.environ.get("DEVBENCH_SQLITE_LIB"):
        lib = _absolute(os.environ["DEVBENCH_SQLITE_LIB"])
        if lib.is_dir():
            yield "LD_LIBRARY_PATH", lib


def _runtime(python, workspace, expected_version):
    command = [str(python), "-I", "-B", "-c", _QUERY]
    env = dict(os.environ)
    env.pop("DYLD_LIBRARY_PATH", None)
    env.pop("LD_LIBRARY_PATH", None)
    server_env = dict(THREADS)

    def probe():
        return subprocess.run(
            command,
            cwd=workspace,
            env={**env, **server_env},
            capture_output=True,
            text=True,
            timeout=15,
        )

    process = probe()
    # Only an unpatched system/Conda runtime opts in to an external SQLite.
    if process.returncode == 78:
        for key, lib in _fallback_libraries():
            server_env[key] = str(lib)
            process = probe()
            if not process.returncode:
                break
    if process.returncode:
        lines = process.stderr.strip().splitlines()
        reason = lines[-1][:300] if lines else "runtime probe failed"
        raise ValueError(
            f"Selected Python lacks a usable DevHarness runtime: {reason}. "
            "Run uv python install and uv sync --locked --extra dev. "
            "macOS SIP can strip DYLD_LIBRARY_PATH from system launchers."
        )
    info = json.loads(process.stdout)
    if expected_version is not None and info["backend_version"] != expected_version:
        raise ValueError(
            "Installed backend version does not match the requested version"
        )
    return info, server_env


def _detect_image(images, workspace, preferred=()):
    docker = shutil.which("docker")
    if docker is None or not images:
        return None
    try:
        result = subprocess.run(
            [
                docker,
                "image",
                "inspect",
                "--format",
                "{{.Id}} {{.Architecture}}",
                *images,
            ],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    architectures = {
        "arm64": "arm64",
        "aarch64": "arm64",
        "x86_64": "amd64",
        "amd64": "amd64",
    }
    native = architectures.get(platform.machine().lower())
    visible = {
        fields[0]
        for line in result.stdout.splitlines()
        if len(fields := line.split()) == 2
        and fields[0] in images
        and fields[1] == native
    }
    preferred_visible = visible & set(preferred)
    if len(preferred_visible) == 1:
        return next(iter(preferred_visible))
    return next(iter(visible)) if len(visible) == 1 else None


def _marketplace(path, output, prohibited):
    path = _absolute(path)
    if path.name == "marketplace.json":
        if path.parent.parts[-2:] != (".agents", "plugins"):
            raise ValueError(
                "Use a marketplace directory or its .agents/plugins/marketplace.json"
            )
        root = path.parents[2]
    else:
        root = path
        path = _absolute(root / ".agents" / "plugins" / "marketplace.json")
    if (
        path.exists()
        or path.is_symlink()
        or not output.is_relative_to(root)
        or root == output
        or any(_overlap(root, other) for other in prohibited)
        or root in {Path(root.anchor), Path.home().resolve()}
    ):
        raise ValueError(
            "Use a new marketplace above the plugin, separate from data and source"
        )
    return path, {
        "name": "devharness-local",
        "interface": {"displayName": "DevHarness Local"},
        "plugins": [
            {
                "name": "devharness",
                "source": {
                    "source": "local",
                    "path": "./" + output.relative_to(root).as_posix(),
                },
                "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                "category": "Education & Research",
            }
        ],
    }


def _configuration_paths(python, input_root, workspace, output):
    python = _absolute(python, executable=True)
    input_root, workspace, output = map(_absolute, (input_root, workspace, output))
    home = Path.home().resolve()
    for path, label in ((input_root, "input root"), (workspace, "workspace")):
        if (
            not path.is_dir()
            or path in {Path(path.anchor), home}
            or _overlap(path, PLUGIN_ROOT)
            or _overlap(path, SKILL_ROOT)
        ):
            raise ValueError(
                f"Choose an existing {label} separate from home, root and source"
            )
    if _overlap(input_root, workspace):
        raise ValueError("Input root and mutable workspace must be separate")
    if output.exists() or output.name != "devharness":
        raise ValueError("Use a new output directory named devharness")
    if any(
        _overlap(output, path)
        for path in (input_root, workspace, PLUGIN_ROOT, SKILL_ROOT)
    ):
        raise ValueError(
            "Plugin output must be separate from its source, input root and workspace"
        )
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError(
            "Select an existing executable Python from the installed runtime"
        )
    return python, input_root, workspace, output


@contextmanager
def _directory(path, *, create=False):
    """Pin each path component before opening its child, including ancestors."""
    path = _absolute(path)
    flags = os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW
    with ExitStack() as stack:
        fd = os.open(path.anchor, flags)
        stack.callback(os.close, fd)
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            fd = os.open(part, flags, dir_fd=fd)
            stack.callback(os.close, fd)
        yield fd


def _read_regular(directory, name):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    descriptor = os.open(name, flags, dir_fd=directory)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Package and mirror files must be regular, unlinked files")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            return source.read()
    finally:
        os.close(descriptor)


def _read_tree(directory, parts, payloads):
    for name in sorted(os.listdir(directory)):
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child = os.open(
                name, os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory
            )
            try:
                _read_tree(child, (*parts, name), payloads)
            finally:
                os.close(child)
        else:
            payloads[(*parts, name)] = _read_regular(directory, name)


def _skills(root):
    payloads = {}
    for name in SKILL_NAMES:
        with _directory(root / name) as directory:
            _read_tree(directory, (name,), payloads)
        if (name, "SKILL.md") not in payloads:
            raise ValueError(f"Missing skill entry point for {name}")
    return payloads


def _json_bytes(value):
    return (json.dumps(value, indent=2) + "\n").encode()


def _compatibility_manifest(manifest):
    metadata = {
        key: value
        for key, value in manifest.items()
        if key not in {"$schema", "extensions"}
    }
    return {
        **metadata,
        "skills": "./skills/",
        "mcpServers": "./.mcp.json",
        "interface": manifest["extensions"]["com.openai"]["interface"],
    }


def _source_package(*, legacy=True):
    with _directory(PLUGIN_ROOT) as fd:
        payloads = {
            (name,): _read_regular(fd, name)
            for name in ("plugin.json", "README.md", "LICENSE")
        }
    with _directory(PLUGIN_ROOT / "scripts") as fd:
        payloads["scripts", "configure.py"] = _read_regular(fd, "configure.py")
    if legacy:
        manifest = json.loads(payloads[("plugin.json",)])
        payloads[".codex-plugin", "plugin.json"] = _json_bytes(
            _compatibility_manifest(manifest)
        )
    for parts, data in _skills(SKILL_ROOT).items():
        payloads[("skills", *parts)] = data
    return payloads


def _digest(data):
    return hashlib.sha256(data).hexdigest()


_MIRROR_TARGETS = frozenset({".agents/skills", ".claude/skills", ".cursor/skills"})


def _recorded_skill(key, targets):
    parts = key.split("/")
    target = "/".join(parts[:2])
    if (
        target not in targets
        or len(parts) < 4
        or parts[2] not in SKILL_NAMES
        or any(p in {"", ".", ".."} or "\x00" in p for p in parts)
    ):
        raise ValueError("Invalid managed path in skill mirror manifest")
    return target, tuple(parts[2:])


def _record(root):
    with _directory(root) as directory:
        record = json.loads(_read_regular(directory, MIRROR_MANIFEST))
    if (
        not isinstance(record, dict)
        or record.get("format") != "devharness.skill-mirrors/1"
        or not isinstance(record.get("targets"), list)
        or not record["targets"]
        or not all(
            isinstance(t, str) and t in _MIRROR_TARGETS for t in record["targets"]
        )
        or not isinstance(record.get("files"), dict)
        or not all(isinstance(entry, dict) for entry in record["files"].values())
    ):
        raise ValueError("Invalid skill mirror manifest")
    for key, entry in record["files"].items():
        _recorded_skill(key, record["targets"])
        digest = entry.get("mirror_sha256")
        if (
            not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or digest != entry.get("source_sha256")
        ):
            raise ValueError("Invalid checksum in skill mirror manifest")
    return record


def _mirror_record(root, sources, targets):
    files = {}
    for target in sorted(targets):
        observed = _skills(root / target)
        if observed != sources:
            raise ValueError(f"Skill mirror drift in {target}")
        for parts, data in sorted(sources.items()):
            filename = "/".join((target, *parts))
            files[filename] = {
                "source_sha256": _digest(data),
                "mirror_sha256": _digest(observed[parts]),
            }
    return {
        "format": "devharness.skill-mirrors/1",
        "targets": sorted(targets),
        "files": files,
    }


def check_mirrors(root):
    root = _absolute(root)
    with _directory(root) as directory:
        fcntl.flock(directory, fcntl.LOCK_SH)
        saved = _record(root)
        if saved != _mirror_record(root, _skills(SKILL_ROOT), saved["targets"]):
            raise ValueError("Skill mirror checksums differ from the manifest")
        return saved


def _atomic_file(directory, name, data):
    # The temporary file is new and the rename stays in the pinned directory.
    temporary = f".devharness-{os.urandom(16).hex()}.tmp"
    fd = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=directory,
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


def _copy_skill(root, target, parts, data, previous):
    destination = root.joinpath(target, *parts)
    # Never modify the checkout's canonical skills through a mirror operation.
    if destination == SKILL_ROOT.joinpath(*parts):
        return
    with _directory(destination.parent, create=True) as directory:
        try:
            old = _read_regular(directory, destination.name)
        except FileNotFoundError:
            old = None
        if old == data:
            return
        key = destination.relative_to(root).as_posix()
        if old is not None and previous.get(key, {}).get("mirror_sha256") != _digest(
            old
        ):
            raise ValueError(f"Existing client skill differs from recorded copy: {key}")
        _atomic_file(directory, destination.name, data)


def _prune_obsolete(root, sources, previous):
    for key, entry in previous["files"].items():
        target, parts = _recorded_skill(key, previous["targets"])
        if parts in sources or root / target == SKILL_ROOT:
            continue
        path = root.joinpath(target, *parts)
        try:
            with _directory(path.parent) as directory:
                current = _read_regular(directory, path.name)
                if _digest(current) != entry["mirror_sha256"]:
                    raise ValueError(f"Obsolete mirror has local changes: {key}")
                os.unlink(path.name, dir_fd=directory)
        except FileNotFoundError:
            # A previous attempt may have removed this entry before discovering
            # drift elsewhere. The saved manifest advances only after parity.
            continue


def _client_targets(clients, legacy_cursor):
    selected = set(clients.split(","))
    if "all" in selected:
        if selected != {"all"}:
            raise ValueError("Use all alone, or a comma-separated list of clients")
        selected = {"claude", "cursor"}
    if not selected <= {"claude", "cursor"}:
        raise ValueError("Supported mirror clients are claude, cursor and all")
    if legacy_cursor and "cursor" not in selected:
        raise ValueError("--legacy-cursor-skills requires the cursor client")
    targets = set()
    if "claude" in selected:
        targets.add(".claude/skills")
    if "cursor" in selected:
        targets.add(".agents/skills")
        if legacy_cursor:
            targets.add(".cursor/skills")
    return targets


def _mirror_scope(root, targets, protected):
    if root == Path(root.anchor):
        raise ValueError("Choose a workspace for client mirrors")
    for target in {*targets, MIRROR_MANIFEST}:
        path = root / target
        if any(_overlap(path, p) for p in (*protected, PLUGIN_ROOT)):
            raise ValueError("Keep mirrors separate from inputs, artifacts and plugin")
        if _overlap(path, SKILL_ROOT) and path != SKILL_ROOT:
            raise ValueError("Mirror destinations must stay outside canonical skills")


def mirror_skills(root, clients, legacy_cursor=False, *, protected=()):
    root = _absolute(root)
    targets = _client_targets(clients, legacy_cursor)
    _mirror_scope(root, targets, protected)
    with _directory(root, create=True) as directory:
        fcntl.flock(directory, fcntl.LOCK_EX)
        try:
            previous = _record(root)
        except FileNotFoundError:
            previous = {"targets": [], "files": {}}
        targets.update(previous["targets"])
        _mirror_scope(root, targets, protected)
        sources = _skills(SKILL_ROOT)
        _prune_obsolete(root, sources, previous)
        for target in sorted(targets):
            for parts, data in sources.items():
                _copy_skill(root, target, parts, data, previous["files"])
        record = _mirror_record(root, sources, targets)
        _atomic_file(directory, MIRROR_MANIFEST, _json_bytes(record))
        return record


def _write_new(path, content, mode=0o600):
    with _directory(path.parent, create=True) as directory:
        fd = os.open(
            path.name,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
            mode,
            dir_fd=directory,
        )
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)


def _write_package(output, payloads, marketplace):
    with _directory(output.parent, create=True) as parent:
        os.mkdir(output.name, mode=0o700, dir_fd=parent)
    try:
        for parts, content in payloads.items():
            mode = 0o700 if parts == ("scripts", "devbench-python") else 0o600
            _write_new(output.joinpath(*parts), content, mode)
        if marketplace is not None:
            path, document = marketplace
            _write_new(path, _json_bytes(document))
    except BaseException:
        shutil.rmtree(output)
        raise


def _server_payloads(python, workspace, arguments, server_env):
    """Map one STDIO invocation to both client formats."""
    server = {
        "command": str(python),
        "args": arguments,
        "cwd": str(workspace),
        "env": server_env,
    }
    # The portable command must be bare or plugin-relative; an absolute
    # workspace cwd is also disallowed. Keep exact runtime binding in a launcher.
    launcher = ["#!/bin/sh", "set -eu", f"cd {shlex.quote(str(workspace))}"]
    for key in ("DYLD_LIBRARY_PATH", "LD_LIBRARY_PATH"):
        if key in server_env:
            launcher.append(f"export {key}={shlex.quote(server_env[key])}")
    launcher.append(f'exec {shlex.quote(str(python))} "$@"')
    portable = {
        "type": "stdio",
        "command": "./scripts/devbench-python",
        "args": arguments,
        "env": server_env,
    }
    return {
        (".mcp.json",): _json_bytes({"mcpServers": {"devharness": server}}),
        ("mcp.json",): _json_bytes(
            {"$schema": MCP_SCHEMA, "mcpServers": {"devharness": portable}}
        ),
        ("scripts", "devbench-python"): ("\n".join(launcher) + "\n").encode(),
    }


def configure(
    python,
    input_root,
    workspace,
    output,
    *,
    expected_version=None,
    mounted_image_id=None,
    write_marketplace=None,
    allow_external_input_files=False,
    legacy_codex_manifest=True,
):
    python, input_root, workspace, output = _configuration_paths(
        python,
        input_root,
        workspace,
        output,
    )
    info, server_env = _runtime(python, workspace, expected_version)
    backend_paths = [
        Path(origin).resolve().parent for origin in info["backend_origins"]
    ]
    if any(
        _overlap(path, backend)
        for path in (input_root, workspace, output)
        for backend in backend_paths
    ):
        raise ValueError(
            "Keep input root, workspace and output separate from backend source"
        )
    if mounted_image_id is not None and not re.fullmatch(
        r"sha256:[a-f0-9]{64}", mounted_image_id
    ):
        raise ValueError("Mounted image ID must be a complete SHA-256 image digest")
    pinned_images = info.pop("pinned_images")
    preferred_images = info.pop("preferred_images", ())
    image = mounted_image_id or _detect_image(
        pinned_images, workspace, preferred=preferred_images
    )
    marketplace = None
    if write_marketplace is not None:
        marketplace = _marketplace(
            write_marketplace,
            output,
            [input_root, workspace, PLUGIN_ROOT, SKILL_ROOT, *backend_paths],
        )
    payloads = _source_package(legacy=legacy_codex_manifest)
    arguments = [
        "-I",
        "-B",
        "-m",
        "devbench.platform.cli",
        "mcp",
        "--input-root",
        str(input_root),
        "--workspace",
        str(workspace),
        "--no-auto-browser",
    ]
    if image:
        arguments += ["--mounted-image-id", image]
    if allow_external_input_files:
        arguments.append("--allow-external-input-files")
    payloads.update(_server_payloads(python, workspace, arguments, server_env))
    result = {
        "format": "devharness.local-plugin-binding/1",
        "plugin": str(output),
        "input_root": str(input_root),
        "workspace": str(workspace),
        "python": str(python),
        "runtime": info,
        "mounted_image_id": image,
        "installed_into_codex": False,
        "scientific_execution_performed": False,
        "scope": "local_configuration",
    }
    if marketplace is not None:
        result["marketplace"] = str(marketplace[0])
    payloads[("runtime-binding.json",)] = (json.dumps(result, indent=2) + "\n").encode()
    _write_package(output, payloads, marketplace)
    return result


def _binding_paths(args, parser):
    paths = (args.python, args.input_root, args.workspace, args.output)
    if any(paths) and not all(paths):
        parser.error(
            "A binding requires --python, --input-root, --workspace and --output"
        )
    if not any(paths) and not (args.mirror_clients or args.check_mirrors):
        parser.error("Supply binding paths, --mirror-clients or --check-mirrors")
    if args.legacy_cursor_skills and not args.mirror_clients:
        parser.error("--legacy-cursor-skills requires --mirror-clients cursor or all")
    if any(paths) and args.check_mirrors:
        parser.error(
            "--check-mirrors verifies existing mirrors without configuring a plugin"
        )
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    binding = parser.add_argument_group("local plugin binding (supply all four paths)")
    for name in ("python", "input-root", "workspace", "output"):
        binding.add_argument(f"--{name}", type=Path)
    parser.add_argument("--expect-version")
    parser.add_argument("--mounted-image-id")
    parser.add_argument("--write-marketplace", type=Path)
    parser.add_argument("--allow-external-input-files", action="store_true")
    parser.add_argument("--no-legacy-codex-manifest", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--mirror-clients", metavar="claude,cursor,all")
    mode.add_argument("--check-mirrors", action="store_true")
    parser.add_argument("--legacy-cursor-skills", action="store_true")
    parser.add_argument("--mirror-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    paths = _binding_paths(args, parser)
    try:
        result = {}
        # Install retriable mirrors first. A failed mirror must leave the fresh
        # output path available for the binding on the next invocation.
        if args.mirror_clients:
            protected = _configuration_paths(*paths)[1:] if all(paths) else ()
            result["mirrors"] = mirror_skills(
                args.mirror_root,
                args.mirror_clients,
                args.legacy_cursor_skills,
                protected=protected,
            )
        elif args.check_mirrors:
            result["mirrors"] = check_mirrors(args.mirror_root)
        if all(paths):
            result.update(
                configure(
                    *paths,
                    expected_version=args.expect_version,
                    mounted_image_id=args.mounted_image_id,
                    write_marketplace=args.write_marketplace,
                    allow_external_input_files=args.allow_external_input_files,
                    legacy_codex_manifest=not args.no_legacy_codex_manifest,
                )
            )
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        parser.exit(2, str(error) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
