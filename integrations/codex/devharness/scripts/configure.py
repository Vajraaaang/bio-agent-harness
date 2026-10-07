"""Create a machine-specific local plugin copy. Does not install or run tools."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
THREADS = {key: "1" for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                               "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS", "NUMBA_NUM_THREADS")}
FILES = ((".codex-plugin", "plugin.json"), ("skills", "rna-workbench", "SKILL.md"),
         ("scripts", "configure.py"), ("README.md",), ("LICENSE",))


def _absolute(value):
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("Use an absolute path")
    return path


def _overlap(first, second):
    return first.is_relative_to(second) or second.is_relative_to(first)


def _runtime(python, workspace, expected_version):
    # -I avoids imports from the dataset folder or PYTHONPATH. Preserve the
    # venv executable spelling: resolving its symlink can select base Python.
    query = (
        "import importlib.metadata as m, importlib.util as u, json, sys; "
        "s=u.find_spec('devharness_server'); "
        "assert sys.version_info >= (3,11), 'Python 3.11+ required'; "
        "assert s and s.origin and u.find_spec('mcp'), 'Install the DevHarness runtime first'; "
        "print(json.dumps({'backend_version':m.version('devharness'), "
        "'mcp_version':m.version('mcp'), 'backend_origin':s.origin, "
        "'python_version':sys.version.split()[0]}))"
    )
    process = subprocess.run([str(python), "-I", "-B", "-c", query], cwd=str(workspace),
        capture_output=True, text=True, timeout=15)
    if process.returncode:
        raise ValueError("Selected Python lacks a usable DevHarness/MCP installation")
    info = json.loads(process.stdout)
    if expected_version is not None and info["backend_version"] != expected_version:
        raise ValueError("Installed backend version does not match the requested version")
    package_root = Path(info["backend_origin"]).resolve().parent
    if _overlap(workspace, package_root):
        raise ValueError("Analysis workspace must not contain or be inside backend code")
    return info


def configure(python, workspace, output, *, expected_version=None):
    python = _absolute(python)
    python = Path(os.path.abspath(python))
    workspace = _absolute(workspace).resolve()
    output = _absolute(output).resolve()
    if not workspace.is_dir() or workspace in {Path(workspace.anchor), Path.home().resolve()}:
        raise ValueError("Choose an existing dedicated analysis workspace, not the home or filesystem root")
    if _overlap(workspace, PLUGIN_ROOT):
        raise ValueError("Analysis workspace must be separate from the plugin package")
    if output.exists() or output.name != "devharness":
        raise ValueError("Use a new output directory named devharness")
    if _overlap(output, PLUGIN_ROOT) or _overlap(output, workspace):
        raise ValueError("New plugin copy must be separate from its source and analysis workspace")
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError("Select an existing executable Python from the installed runtime")
    info = _runtime(python, workspace, expected_version)
    payloads = {}
    for parts in FILES:
        source = PLUGIN_ROOT.joinpath(*parts)
        if source.is_symlink() or not source.resolve().is_relative_to(PLUGIN_ROOT):
            raise ValueError("Plugin source files must remain inside the package")
        payloads[parts] = source.read_bytes()
    manifest = json.loads(payloads[(".codex-plugin", "plugin.json")])
    manifest["mcpServers"] = {"devharness": {"command": str(python),
        "args": ["-I", "-B", "-m", "devharness_server.server"],
        "cwd": str(workspace), "env": THREADS}}
    payloads[(".codex-plugin", "plugin.json")] = (json.dumps(manifest, indent=2) + "\n").encode()
    output.mkdir(parents=True, exist_ok=False)
    for parts, content in payloads.items():
        destination = output.joinpath(*parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as handle:
            handle.write(content)
    result = {"format": "devharness.local-plugin-binding/1", "plugin": str(output),
        "workspace": str(workspace), "python": str(python), "runtime": info,
        "installed_into_codex": False, "scientific_execution_performed": False,
        "scope": "local_configuration_not_live_Codex_or_scientific_qualification"}
    with (output / "runtime-binding.json").open("x") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expect-version")
    args = parser.parse_args()
    try:
        result = configure(args.python, args.workspace, args.output, expected_version=args.expect_version)
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        parser.exit(2, str(error) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
