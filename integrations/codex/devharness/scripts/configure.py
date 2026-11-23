"""Create a machine-specific local DevHarness plugin copy."""

import argparse
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_SQLITE_LIB = Path("/opt/homebrew/opt/sqlite/lib")
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
_REFERENCE_PARTS = ("skills", "rna-workbench", "references")
FILES = (
    (".codex-plugin", "plugin.json"),
    (".mcp.json",),
    ("skills", "rna-workbench", "SKILL.md"),
    ("scripts", "configure.py"),
    ("README.md",),
    ("LICENSE",),
)
_QUERY = """
import importlib.metadata as metadata
import importlib.util
import json
import sqlite3
import sys

if sys.version_info < (3, 13):
    raise SystemExit("Python 3.13+ is required")
from devbench.engine.scientific_authority import require_patched_wal
from devharness_controlled.native_contracts import limma_voom_kernel_images
require_patched_wal()
packages = (
    "devbench", "devharness_core", "devharness_server",
    "devharness_controlled", "rna_kernels",
)
print(json.dumps({
    "backend_version": metadata.version("devharness"),
    "mcp_version": metadata.version("mcp"),
    "backend_origins": [importlib.util.find_spec(name).origin for name in packages],
    "python_version": sys.version.split()[0],
    "sqlite_version": sqlite3.sqlite_version,
    "pinned_images": sorted(limma_voom_kernel_images())
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


def _runtime(python, workspace, expected_version):
    command = [str(python), "-I", "-B", "-c", _QUERY]
    env = dict(os.environ)
    server_env = dict(THREADS)
    if sys.platform == "darwin":
        env.pop("DYLD_LIBRARY_PATH", None)
    process = subprocess.run(
        command, cwd=workspace, env=env, capture_output=True, text=True, timeout=15
    )
    if process.returncode and sys.platform == "darwin" and _SQLITE_LIB.is_dir():
        server_env["DYLD_LIBRARY_PATH"] = str(_SQLITE_LIB)
        process = subprocess.run(
            command,
            cwd=workspace,
            env={**env, **server_env},
            capture_output=True,
            text=True,
            timeout=15,
        )
    if process.returncode:
        lines = process.stderr.strip().splitlines()
        reason = lines[-1][:300] if lines else "runtime probe failed"
        raise ValueError(f"Selected Python lacks a usable DevHarness runtime: {reason}")
    info = json.loads(process.stdout)
    if expected_version is not None and info["backend_version"] != expected_version:
        raise ValueError(
            "Installed backend version does not match the requested version"
        )
    return info, server_env


def _detect_image(images, workspace):
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
        ):
            raise ValueError(
                f"Choose an existing {label} separate from home, root and source"
            )
    if _overlap(input_root, workspace):
        raise ValueError("Input root and mutable workspace must be separate")
    if output.exists() or output.name != "devharness":
        raise ValueError("Use a new output directory named devharness")
    if any(_overlap(output, path) for path in (input_root, workspace, PLUGIN_ROOT)):
        raise ValueError(
            "Plugin output must be separate from its source, input root and workspace"
        )
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError(
            "Select an existing executable Python from the installed runtime"
        )
    return python, input_root, workspace, output


def _reference_payloads() -> dict[tuple[str, ...], bytes]:
    root = _absolute(PLUGIN_ROOT)
    folder = root.joinpath(*_REFERENCE_PARTS)
    if folder.is_symlink() or folder.resolve() != folder:
        raise ValueError("Plugin reference directory must use canonical package paths")
    directory_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY
    try:
        with ExitStack() as stack:
            descriptor = os.open(root, directory_flags)
            stack.callback(os.close, descriptor)
            for part in _REFERENCE_PARTS:
                try:
                    child = os.open(part, directory_flags, dir_fd=descriptor)
                except FileNotFoundError:
                    if part == "references":
                        return {}
                    raise
                stack.callback(os.close, child)
                descriptor = child
            return {
                (*_REFERENCE_PARTS, name): _read_reference(descriptor, name)
                for name in sorted(os.listdir(descriptor))
                if name.endswith(".md")
            }
    except OSError as error:
        raise ValueError(
            "Plugin reference files must stay inside the package"
        ) from error


def _read_reference(descriptor: int, name: str) -> bytes:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    file_descriptor = os.open(name, flags, dir_fd=descriptor)
    with os.fdopen(file_descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Plugin references must be regular package files")
        return source.read()


def _source_package():
    payloads = {}
    for parts in FILES:
        source = PLUGIN_ROOT.joinpath(*parts)
        if source != source.resolve():
            raise ValueError("Plugin source files must remain inside the package")
        payloads[parts] = source.read_bytes()
    return {**payloads, **_reference_payloads()}


def _write_package(output, payloads, marketplace):
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    try:
        for parts, content in payloads.items():
            destination = output.joinpath(*parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as handle:
                handle.write(content)
        if marketplace is not None:
            path, document = marketplace
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8") as handle:
                handle.write(json.dumps(document, indent=2) + "\n")
    except BaseException:
        shutil.rmtree(output)
        raise


def configure(
    python,
    input_root,
    workspace,
    output,
    *,
    expected_version=None,
    mounted_image_id=None,
    write_marketplace=None,
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
    image = mounted_image_id or _detect_image(pinned_images, workspace)
    marketplace = None
    if write_marketplace is not None:
        marketplace = _marketplace(
            write_marketplace,
            output,
            [input_root, workspace, PLUGIN_ROOT, *backend_paths],
        )
    payloads = _source_package()
    arguments = [
        "-I",
        "-B",
        "-m",
        "devbench.platform.plugin_mcp",
        "--input-root",
        str(input_root),
        "--workspace",
        str(workspace),
    ]
    if image:
        arguments += ["--mounted-image-id", image]
    mcp = {
        "mcpServers": {
            "devharness": {
                "command": str(python),
                "args": arguments,
                "cwd": str(workspace),
                "env": server_env,
            }
        }
    }
    payloads[(".mcp.json",)] = (json.dumps(mcp, indent=2) + "\n").encode()
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--input-root", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expect-version")
    parser.add_argument("--mounted-image-id")
    parser.add_argument("--write-marketplace", type=Path)
    args = parser.parse_args()
    try:
        result = configure(
            args.python,
            args.input_root,
            args.workspace,
            args.output,
            expected_version=args.expect_version,
            mounted_image_id=args.mounted_image_id,
            write_marketplace=args.write_marketplace,
        )
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        parser.exit(2, str(error) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
