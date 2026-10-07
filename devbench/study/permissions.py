"""Explicit participant read boundary, separate from worker resource limits.

Constructing this configuration does not start Codex or prove OS enforcement.
The executing coordinator must probe the same profile before every live run.
"""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import subprocess
import sysconfig
import socket


def _absolute(value):
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("Permission paths must be absolute")
    return path.resolve()


def _intersects(left, right):
    return left.is_relative_to(right) or right.is_relative_to(left)


def platform_scratch_roots():
    roots = {Path(value).resolve() for value in ("/tmp", "/private/tmp", "/var/tmp", "/private/var/tmp")}
    configured = os.environ.get("TMPDIR")
    if configured and Path(configured).is_absolute():
        roots.add(Path(configured).resolve())
    return tuple(sorted(roots))


def require_protected_storage(path):
    path = _absolute(path)
    if any(path.is_relative_to(root) for root in platform_scratch_roots()):
        raise ValueError("Protected benchmark storage must be outside platform scratch directories")
    return path


def _toml(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, dict):
        return "{" + ", ".join(json.dumps(key) + " = " + _toml(item)
                                for key, item in value.items()) + "}"
    raise TypeError("Unsupported permission configuration value")


@dataclass(frozen=True)
class PermissionBoundary:
    workspace: Path
    private_roots: tuple[Path, ...] = field(default_factory=tuple)
    runtime_roots: tuple[Path, ...] = field(default_factory=tuple)
    protected_paths: tuple[Path, ...] = field(default_factory=tuple)
    socket_path: Path | None = None

    def __post_init__(self):
        workspace = require_protected_storage(self.workspace)
        object.__setattr__(self, "workspace", workspace)
        for name in ("private_roots", "runtime_roots", "protected_paths"):
            values = tuple(sorted({_absolute(value) for value in getattr(self, name)}))
            object.__setattr__(self, name, values)
        for path in self.private_roots:
            require_protected_storage(path)
            if _intersects(path, workspace):
                raise ValueError("Private state and participant workspace must be disjoint")
        for path in self.runtime_roots:
            if path == Path("/") or Path.home().resolve().is_relative_to(path):
                raise ValueError("Runtime read grant is too broad")
            if any(_intersects(path, private) for private in self.private_roots):
                raise ValueError("Runtime read grant intersects private state")
        for path in self.protected_paths:
            if path == workspace or not path.is_relative_to(workspace):
                raise ValueError("Protected paths must be below the participant workspace")
        if self.socket_path is not None:
            path = _absolute(self.socket_path)
            if not path.is_relative_to(workspace) or path.parent == workspace:
                raise ValueError("Tool socket needs a protected subdirectory in the workspace")
            object.__setattr__(self, "socket_path", path)

    def config_overrides(self):
        filesystem = {":root": "deny", ":minimal": "read", ":tmpdir": "deny", ":slash_tmp": "deny"}
        # Python's importer lists the stdlib directory before visiting admitted
        # children. Permit that directory only for a complete admitted inventory,
        # while denying the package trees deliberately excluded by _runtime_roots.
        stdlib = Path(sysconfig.get_path("stdlib")).resolve()
        roots = set(self.runtime_roots)
        covered_by_stdlib = set()
        if any(path.parent == stdlib for path in roots):
            inventory = {path.resolve() for path in stdlib.iterdir()
                         if path.name not in {"site-packages", "dist-packages", "__pycache__"}}
            if inventory and inventory <= roots:
                filesystem[str(stdlib)] = {".": "read", "site-packages": "deny", "dist-packages": "deny"}
                covered_by_stdlib = {path for path in roots if path.is_relative_to(stdlib)
                                    and not any(path.is_relative_to(stdlib / name)
                                                for name in ("site-packages", "dist-packages"))}
        filesystem.update({str(path): "read" for path in self.runtime_roots if path not in covered_by_stdlib})
        filesystem[str(self.workspace)] = "write"
        filesystem.update({str(path): "read" for path in self.protected_paths})
        filesystem.update({str(path): "deny" for path in self.private_roots})
        network = {"enabled": False}
        if self.socket_path is not None:
            filesystem[str(self.socket_path.parent)] = "read"
            network.update(enabled=True, domains={}, allow_upstream_proxy=False)
            network["unix_sockets"] = {str(self.socket_path): "allow"}
        profile = {"description": "Restricted benchmark assets; platform scratch exception disclosed separately.",
                   "extends": ":workspace", "filesystem": filesystem, "network": network}
        return (["features.network_proxy=true"] if self.socket_path is not None else []) + [
            "default_permissions=" + _toml("devbench_study"),
            "permissions=" + _toml({"devbench_study": profile}),
            'approval_policy="never"', 'web_search="disabled"',
            "project_doc_max_bytes=0",
        ]

    def exec_arguments(self):
        arguments = ["--strict-config", "--ignore-user-config", "--ignore-rules", "--ephemeral"]
        # Benchmark participants must not inherit optional automation, memories,
        # or delegation from unrelated sessions. Managed requirements still apply.
        for feature in ("hooks", "memories", "multi_agent", "shell_snapshot"):
            arguments.extend(["-c", f"features.{feature}=false"])
        # Login initialization and snapshots can replace the declared child PATH.
        arguments.extend(["-c", "allow_login_shell=false"])
        # Skills are discovered independently of user config and AGENTS.md.
        # Keep unrelated skill instructions out of the controlled participant.
        arguments.extend(["-c", "skills.include_instructions=false"])
        for override in self.config_overrides():
            arguments.extend(["-c", override])
        return arguments

    def execution_environment(self, base=None):
        """Select the same stricter profile despite an inherited CLI selector.

        Preserve all other sandbox/managed settings. This selects the declared
        profile consistently; it never disables policy. A boundary probe remains
        necessary rather than assuming configuration proves enforcement.
        """
        environment = dict(os.environ if base is None else base)
        environment["CODEX_PERMISSION_PROFILE"] = "devbench_study"
        return environment

    def sandbox_arguments(self, executable, command):
        """Arguments for a future non-model boundary probe, never run on import."""
        executable = _absolute(executable)
        if not command or not Path(command[0]).is_absolute():
            raise ValueError("Sandbox probe requires an absolute executable")
        arguments = [str(executable), "sandbox", "--permission-profile", "devbench_study",
                     "--include-managed-config", "--cd", str(self.workspace)]
        for override in self.config_overrides():
            arguments.extend(["-c", override])
        if self.socket_path is not None:
            arguments.extend(["--allow-unix-socket", str(self.socket_path)])
        return [*arguments, "--", *map(str, command)]

    def describe(self):
        return {"enforcement": "codex_named_permission_profile", "default_read": "deny",
                "workspace": str(self.workspace), "configuration_status": "constructed",
                "runtime_preflight_status": "not_run", "permission_escalation_allowed": False,
                "filesystem_isolation_verified": False,
                "boundary_scope": "protected_benchmark_assets",
                "ambient_system_scratch_access": "platform_exception_not_eliminated",
                "network_enforcement": "deny_all_domain_proxy_with_one_unix_socket" if self.socket_path else "disabled",
                "network_access": False, "tool_socket": str(self.socket_path) if self.socket_path else None}


def probe_boundary(boundary, codex_executable, probe_python, *, allow_configuration_probe=False):
    """Test empty-file access under the profile; never launch a model or read data.

    This proves the standalone sandbox behavior only. The subsequent participant
    must receive the same configuration; live integration is separate evidence.
    """
    if allow_configuration_probe is not True:
        raise PermissionError("Permission probes are not part of plan-only preparation")
    if not boundary.private_roots:
        raise ValueError("A coordinator-private directory is required for the read probe")
    private = boundary.private_roots[0] / ".devbench-read-probe"
    protected = boundary.workspace / "control" / ".devbench-write-probe"
    writable = boundary.workspace / ".devbench-allowed-write-probe"
    protected.parent.mkdir(parents=True, exist_ok=True)
    effective = PermissionBoundary(workspace=boundary.workspace, private_roots=boundary.private_roots,
        runtime_roots=boundary.runtime_roots,
        protected_paths=(*boundary.protected_paths, protected.parent), socket_path=boundary.socket_path)
    created = []
    listener = None
    try:
        for path in (private, protected, writable):
            with path.open("xb"):
                pass
            created.append(path)
        script = (
            "import json\n"
            "checks={}\n"
            "def permitted(path,mode):\n"
            " try:\n"
            "  with open(path,mode) as handle:\n"
            "   if mode=='rb': handle.read(1)\n"
            "   else: handle.truncate(0)\n"
            "  return True\n"
            " except PermissionError: return False\n"
            f"checks['private_read_denied']=not permitted({str(private)!r},'rb')\n"
            f"checks['protected_write_denied']=not permitted({str(protected)!r},'ab')\n"
            f"checks['workspace_write_allowed']=permitted({str(writable)!r},'ab')\n"
            "print(json.dumps(checks))\n"
        )
        expected = {"private_read_denied", "protected_write_denied", "workspace_write_allowed"}
        if boundary.socket_path is not None:
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            script = script.removesuffix("print(json.dumps(checks))\n") + (
                "import socket,os,urllib.parse\n"
                "proxy=os.environ.get('HTTP_PROXY') or os.environ.get('http_proxy') or ''\n"
                "checks['proxy_environment_present']=urllib.parse.urlparse(proxy).hostname in ('127.0.0.1','localhost','::1')\n"
                "try:\n"
                " connection=socket.socket();connection.settimeout(1)\n"
                f" connection.connect({listener.getsockname()!r});connection.close()\n"
                " checks['direct_tcp_denied']=False\n"
                "except PermissionError: checks['direct_tcp_denied']=True\n"
                "except OSError: checks['direct_tcp_denied']=False\n"
                "print(json.dumps(checks))\n")
            expected |= {"proxy_environment_present", "direct_tcp_denied"}
        command = effective.sandbox_arguments(codex_executable, [str(_absolute(probe_python)), "-I", "-c", script])
        result = subprocess.run(command, cwd=effective.workspace, env=effective.execution_environment(),
                                capture_output=True, text=True, timeout=20)
        try:
            checks = json.loads(result.stdout) if result.returncode == 0 else {}
        except json.JSONDecodeError:
            checks = {}
        verified = result.returncode == 0 and set(checks) == expected and all(value is True for value in checks.values())
        return {"verified": verified, "scope": "standalone_codex_sandbox_empty_file_probe",
                "live_participant_integration": "not_exercised", "model_launched": False,
                "expression_data_read": False, "returncode": result.returncode, "checks": checks,
                "error": result.stderr[-2000:] if not verified else None,
                "configuration": effective.config_overrides()}
    finally:
        if listener is not None:
            listener.close()
        for path in created:
            path.unlink(missing_ok=True)
