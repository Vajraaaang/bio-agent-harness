"""Read a bounded CLI version and bind it to unchanged executable bytes."""
import json
from itertools import islice
from pathlib import Path
import re
import shutil
import stat


def _output(executable, command, limit):
    from devbench_engine.adapters import CodexCLIAdapter
    runner = CodexCLIAdapter(executable=str(executable), timeout_sec=5, max_mem_mb=512)
    code, stdout, _ = runner._run_process(command, executable.parent, runner._environment(), "", 5, parse_events=False)
    if code != 0 or runner._last_process_measurements.get("stdout_bytes", limit+1) > limit:
        raise ValueError("CLI identity command failed or exceeded its output bound")
    return stdout


def _components(path):
    """The official npm launcher delegates to a platform-native payload."""
    if path.suffix != ".js":
        return [path]
    from .evidence import _read
    package = path.parent.parent / "package.json"
    if json.loads(_read(package, 100_000)).get("name") != "@openai/codex":
        raise ValueError("Unsupported JavaScript CLI launcher")
    source = _read(path, 100_000).decode()
    if "findCodexExecutable" not in source or '"bin"' not in source:
        raise ValueError("Unrecognized Codex launcher resolution; native payload cannot be pinned")
    node = shutil.which("node")
    if node is None:
        raise ValueError("Codex npm launcher has no Node runtime")
    node = Path(node).resolve()
    # Resolve package metadata with the launcher's own Node resolution context;
    # no package implementation or Codex model is executed by this query.
    script = r'''const {createRequire}=require('node:module');const p=require('node:path');
const r=createRequire(process.argv[1]);const root=p.resolve(p.dirname(process.argv[1]),'..');
const triples={linux:{x64:'x86_64-unknown-linux-musl',arm64:'aarch64-unknown-linux-musl'},darwin:{x64:'x86_64-apple-darwin',arm64:'aarch64-apple-darwin'},win32:{x64:'x86_64-pc-windows-msvc',arm64:'aarch64-pc-windows-msvc'}};
const triple=triples[process.platform]?.[process.arch];if(!triple)throw Error('unsupported platform');
const pkg='@openai/codex-'+process.platform+'-'+process.arch;let meta=null;
try{meta=r.resolve(pkg+'/package.json')}catch{}
const vendor=meta?p.join(p.dirname(meta),'vendor'):p.join(root,'vendor');
console.log(JSON.stringify([process.execPath,p.join(vendor,triple,'bin',process.platform==='win32'?'codex.exe':'codex'),meta].filter(Boolean)));'''
    resolved = json.loads(_output(node, [str(node), "-e", script, str(path)], 4096))
    if not isinstance(resolved, list) or not 2 <= len(resolved) <= 3 or any(not isinstance(p, str) or not Path(p).is_absolute() for p in resolved):
        raise ValueError("Invalid Codex native-component resolution")
    return [path, package, *(Path(p).resolve() for p in resolved)]


def _digest(path):
    from .evidence import _file_sha256
    return _file_sha256(path, 250_000_000)


def _package_runtime_components(paths):
    """Capture the Unix package PATH prefix from the pinned native payload.

    The official install-context and runtime implementations recognize bin/codex
    and the rust-v0.159.2 CodexCLI.app/Contents/MacOS/codex layout. Both derive the
    prefix from the outer package metadata and existing codex-path directory.
    """
    from .evidence import _read
    prefixes, components = [], []
    for native in paths:
        if native.name != "codex":
            continue
        if native.parent.name == "bin":
            package = native.parent.parent
        elif native.parts[-4:] == ("CodexCLI.app", "Contents", "MacOS", "codex"):
            package = native.parents[3]
            if not (package / "bin").is_dir():
                continue
        else:
            continue
        metadata = package / "codex-package.json"
        if not metadata.is_file():
            continue
        _read(metadata, 100_000)
        components.append(metadata)
        directory = package / "codex-path"
        if not directory.is_dir():
            continue
        if directory.resolve() != directory:
            raise ValueError("Codex package runtime path must be canonical")
        entries = sorted(islice(directory.iterdir(), 33))
        if len(entries) > 32:
            raise ValueError("Codex package runtime inventory exceeds its file bound")
        total = 0
        for entry in entries:
            info = entry.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Codex package runtime components must be regular files")
            total += info.st_size
        if total > 250_000_000:
            raise ValueError("Codex package runtime inventory exceeds its byte bound")
        prefixes.append(str(directory))
        components.extend(entries)
    if len(prefixes) > 1:
        raise ValueError("Codex package runtime path is ambiguous")
    return prefixes, components


def verify_codex_identity(identity):
    for path, digest in identity["component_sha256"].items():
        if _digest(Path(path)) != digest:
            raise ValueError("Pinned Codex executable component changed")
    if "runtime_path_prepends" in identity:
        prefixes, components = _package_runtime_components(map(Path, identity["component_sha256"]))
        if (prefixes != identity["runtime_path_prepends"]
                or any(str(path) not in identity["component_sha256"] for path in components)):
            raise ValueError("Pinned Codex package runtime inventory changed")


def codex_component_hashes(executable):
    components = _components(Path(executable))
    _, runtime = _package_runtime_components(components)
    return {str(path): _digest(path) for path in (*components, *runtime)}


def effective_child_environment(environment, identity):
    """Apply only the captured package prefix, as Codex's prepend_path_entry does."""
    result = dict(environment)
    for prefix in identity.get("runtime_path_prepends", []):
        result["PATH"] = ":".join([prefix, *(entry for entry in result.get("PATH", "").split(":")
                                           if entry and entry != prefix)])
    return result


def read_codex_identity(executable):
    path = Path(executable)
    components = codex_component_hashes(path)
    prefixes, _ = _package_runtime_components(map(Path, components))
    before = components[str(path)]
    stdout = _output(path, [str(path), "--version"], 512)
    version = stdout.strip()
    if not re.fullmatch(r"codex-cli [0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?", version):
        raise ValueError("Codex version output is unavailable or unrecognized")
    identity = {"executable_path": str(path), "executable_sha256": before, "version": version,
                "component_sha256": components,
                "runtime_path_prepends": prefixes,
                "scope": "local_launcher_native_payload_runtime_and_reported_version_not_provider_model_identity"}
    verify_codex_identity(identity)
    return identity
