"""Local execution backends for the daemon. No engine socket enters a worker."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from devbench.core.execution_contracts import (
    ContainerBackend as ContainerBackend,
)
from devbench.core.execution_contracts import (
    ResourceProfile as ResourceProfile,
)
from devharness_controlled.native_runtime import LocalDocker, NativeRuntimeError

from .mounted_worker_registry import BUILTIN_WORKERS


def _capability(name, *, available, isolated, **details):
    return {
        "isolation_backend": name,
        "available": available,
        "oci_isolated": isolated,
        "clinical_release_allowed": False,
        **details,
    }


def _installed_image_ids(runtime, registry):
    from devharness_controlled.native_contracts import kernel_test_platform

    from .exploratory_images import EXPLORATORY_IMAGE_ID, EXPLORATORY_IMAGES

    curated = {
        EXPLORATORY_IMAGE_ID,
        *(image.image_id for image in EXPLORATORY_IMAGES.values()),
    }
    for worker in registry.workers.values():
        curated.update(worker.image_ids)
    rows = runtime._call(["image", "ls", "--no-trunc", "--format", "{{.ID}}"])
    installed = {"sha256:" + row.removeprefix("sha256:") for row in rows.splitlines()}
    # Disclose only catalog pins; a user's unrelated local image names stay private.
    return sorted(
        pin for pin in installed if pin in curated or kernel_test_platform(pin)
    )


class DockerBackend:
    name = "docker"
    nonrecursive = "bind-recursive=disabled"

    def __init__(self, *, registry=BUILTIN_WORKERS, runtime_factory=LocalDocker):
        self.registry = registry
        self.runtime_factory = runtime_factory

    @contextmanager
    def session(self, directory=None, *, endpoint=None):
        options = {"endpoint": endpoint} if endpoint is not None else {}
        runtime = self.runtime_factory(**options)
        with tempfile.TemporaryDirectory(dir=directory, prefix=".oci-client-") as area:
            runtime.isolate_client(Path(area) / "config")
            yield runtime

    def identity(self, runtime):
        daemon_id = runtime._call(["info", "--format", "{{.ID}}"])
        if not re.fullmatch(r"[A-Za-z0-9:._-]{1,256}", daemon_id):
            raise NativeRuntimeError("Mounted Docker daemon identity unavailable")
        return {"endpoint": runtime.endpoint, "daemon_id": daemon_id}

    def probe(self):
        try:
            with self.session() as runtime:
                self.identity(runtime)
                images = _installed_image_ids(runtime, self.registry)
        except (OSError, ValueError, RuntimeError):
            return _capability(
                self.name, available=False, isolated=False, local_image_ids=[]
            )
        return _capability(
            self.name, available=True, isolated=True, local_image_ids=images
        )

    def image_platform(self, runtime, image_id):
        if not re.fullmatch("sha256:[a-f0-9]{64}", image_id):
            raise NativeRuntimeError("A pinned local image ID is required")
        facts = runtime._call(
            [
                "image",
                "inspect",
                image_id,
                "--format",
                '{"id":{{json .Id}},"os":{{json .Os}},"arch":{{json .Architecture}},'
                '"labels":{{json .Config.Labels}}}',
            ],
            json_result=True,
        )
        return _kernel_platform(facts, image_id)

    def verify_image(self, image_id):
        with self.session() as runtime:
            return self.image_platform(runtime, image_id)

    def launch_options(self, runtime, profile):
        return ["--gpus", "all"] if profile.gpu else []

    def run_isolated(
        self,
        stage,
        *,
        request_sha,
        image_id,
        attempt_id,
        worker_module,
        resource_profile,
        data_classification,
        claim_tier,
        epistemic_purpose,
    ):
        from .mounted_kernel_oci import _run_oci

        self.registry.admit(worker_module, resource_profile, image_id)
        return _run_oci(
            self,
            stage,
            request_sha,
            image_id,
            attempt_id,
            worker_module,
            resource_profile,
        )

    def cleanup(self, stage, *, request_sha, image_id, attempt_id):
        from .mounted_kernel_oci import _recover_oci

        return _recover_oci(self, stage, request_sha, image_id, attempt_id)


def _kernel_platform(facts, image_id):
    labels = facts.get("labels") or {}
    if (
        facts.get("id") != image_id
        or facts.get("os") != "linux"
        or facts.get("arch") not in {"arm64", "amd64"}
        or labels.get("org.opencontainers.image.title") != "devbench-rna-kernel"
        or labels.get("org.opencontainers.image.version") != "2.0"
    ):
        raise NativeRuntimeError("Mounted kernel image or platform mismatch")
    return "linux/" + facts["arch"]


class _PodmanCLI:
    def __init__(self, authfile):
        command = shutil.which("podman")
        if sys.platform != "linux" or os.geteuid() == 0 or command is None:
            raise NativeRuntimeError(
                "Podman requires a local rootless Linux installation"
            )
        self.executable = str(Path(command).resolve(strict=True))
        # Podman needs the real user/storage identity. Drop remote connection,
        # passthrough proxy, engine configuration and credential overrides.
        self.env = {
            key: os.environ[key]
            for key in ("HOME", "PATH", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")
            if key in os.environ
        }
        self.env["REGISTRY_AUTH_FILE"] = str(authfile)

    def _call(self, arguments, *, json_result=False, timeout=30):
        # Podman documents the flag as a boolean with no value.
        args = [
            "no-new-privileges" if item == "no-new-privileges:true" else item
            for item in arguments
        ]
        if args[:2] == ["container", "ls"] and args[-1].startswith("name=^/"):
            args[-1] = args[-1].replace("name=^/", "name=^", 1)
        # Arbitrary worker output belongs to the sealed files, not the control plane.
        output = (
            subprocess.DEVNULL if args[:2] == ["start", "--attach"] else subprocess.PIPE
        )
        try:
            result = subprocess.run(
                [self.executable, "--remote=false", *args],
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.DEVNULL,
                env=self.env,
                timeout=timeout,
                check=True,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise NativeRuntimeError("Local Podman operation failed") from error
        if output == subprocess.DEVNULL:
            return ""
        if len(result.stdout) > 4 * 1024**2:
            raise NativeRuntimeError("Podman response exceeds bound")
        return (
            json.loads(result.stdout) if json_result else result.stdout.decode().strip()
        )

    def inspect(self, container):
        (record,) = self._call(["container", "inspect", container], json_result=True)
        # Podman's native inspect omits Docker's leading slash and digest prefix.
        return {
            **record,
            "Name": "/" + record["Name"].removeprefix("/"),
            "Image": "sha256:" + record["Image"].removeprefix("sha256:"),
        }


_REQUIRED_CONTROLLERS = {"cpu", "memory", "pids"}


def _delegation_root(host):
    root = Path("/sys/fs/cgroup")
    if host.get("cgroupManager") == "systemd":
        uid = os.geteuid()
        return root / "user.slice" / f"user-{uid}.slice" / f"user@{uid}.service"
    if host.get("cgroupManager") == "cgroupfs":
        for line in Path("/proc/self/cgroup").read_text().splitlines():
            if line.startswith("0::/") and ".." not in Path(line[3:]).parts:
                return root / line[4:]
    raise NativeRuntimeError("Podman delegated cgroup was not identified")


def _constrain_child(group):
    for name, limit in (
        ("cpu.max", "100000 100000"),
        ("pids.max", "32"),
        ("memory.max", "67108864"),
    ):
        control = group / name
        if not control.is_file():
            return False
        control.write_text(limit)
        if control.read_text().strip() != limit:
            return False
    return True


def _delegated(host):
    """Prove the caller can create and constrain a child on the unified hierarchy."""
    if host.get("cgroupVersion") != "v2" or not _REQUIRED_CONTROLLERS <= set(
        host.get("cgroupControllers") or []
    ):
        return False
    try:
        root = _delegation_root(host)
        if not _REQUIRED_CONTROLLERS <= set(
            (root / "cgroup.subtree_control").read_text().split()
        ):
            return False
        group = Path(tempfile.mkdtemp(prefix="devbench-probe-", dir=root))
        # A cgroup directory contains kernel-owned pseudo-files; rmdir is its
        # cleanup operation. The probe never modifies a parent or moves a task.
        try:
            return _constrain_child(group)
        finally:
            group.rmdir()
    except (OSError, ValueError):
        return False


def _rootless_info(runtime):
    facts = runtime._call(["info", "--format", "json"], json_result=True)
    host = facts["host"]
    if (
        host.get("os") != "linux"
        or host.get("serviceIsRemote", False) is not False
        or host.get("security", {}).get("rootless") is not True
    ):
        raise NativeRuntimeError("Remote or rootful Podman is not admitted")
    return facts


class PodmanBackend(DockerBackend):
    """Rootless local Podman with a checked storage identity and cgroup delegation."""

    name = "podman"
    nonrecursive = "bind-nonrecursive"

    def __init__(self, *, registry=BUILTIN_WORKERS, runtime_factory=_PodmanCLI):
        super().__init__(registry=registry, runtime_factory=runtime_factory)

    @contextmanager
    def session(self, directory=None, *, endpoint=None):
        with tempfile.TemporaryDirectory(
            dir=directory, prefix=".podman-client-"
        ) as area:
            authfile = Path(area) / "auth.json"
            authfile.write_bytes(b'{"auths":{}}')
            authfile.chmod(0o600)
            yield self.runtime_factory(authfile)

    def identity(self, runtime):
        store = _rootless_info(runtime)["store"]
        root = Path(store["graphRoot"])
        if not root.is_absolute() or root.resolve(strict=True) != root:
            raise NativeRuntimeError("Podman graph root is not canonical")
        metadata = root.stat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o022
        ):
            raise NativeRuntimeError("Podman graph root ownership unverified")
        values = [
            str(root),
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_uid,
            store["graphDriverName"],
        ]
        identity = hashlib.sha256(json.dumps(values).encode()).hexdigest()
        return {"endpoint": str(root), "storage_id": identity}

    def probe(self):
        try:
            with self.session() as runtime:
                self.identity(runtime)
                delegated = _delegated(_rootless_info(runtime)["host"])
                images = _installed_image_ids(runtime, self.registry)
        except (OSError, ValueError, RuntimeError, KeyError):
            return _capability(
                self.name,
                available=False,
                isolated=False,
                cgroups_v2_delegated=False,
                local_image_ids=[],
            )
        return _capability(
            self.name,
            available=True,
            isolated=delegated,
            cgroups_v2_delegated=delegated,
            local_image_ids=images,
        )

    def image_platform(self, runtime, image_id):
        if not re.fullmatch("sha256:[a-f0-9]{64}", image_id):
            raise NativeRuntimeError("A pinned local image ID is required")
        (facts,) = runtime._call(["image", "inspect", image_id], json_result=True)
        return _kernel_platform(
            {
                "id": "sha256:" + facts["Id"].removeprefix("sha256:"),
                "os": facts["Os"],
                "arch": facts["Architecture"],
                "labels": facts.get("Labels") or facts.get("Config", {}).get("Labels"),
            },
            image_id,
        )

    def launch_options(self, runtime, profile):
        if not _delegated(_rootless_info(runtime)["host"]):
            raise NativeRuntimeError(
                "Rootless Podman cgroups v2 delegation unavailable"
            )
        options = [
            "--userns=keep-id",
            "--read-only-tmpfs=false",
            "--http-proxy=false",
            "--no-hosts",
            "--image-volume=ignore",
        ]
        if profile.gpu:
            options += ["--device", "nvidia.com/gpu=all"]
        return options


def _local_files():
    roots = (
        Path(__file__).resolve().parents[2],  # installed project source / packages
        Path(sys.prefix).resolve(),
        Path(sys.base_prefix).resolve(),
    )
    if any(root in (Path("/"), Path.home()) for root in roots):
        raise NativeRuntimeError(
            "Local environment requires a dedicated runtime directory"
        )
    return roots


def _local_pin(registry):
    from importlib import metadata, util
    from platform import machine

    lock = _local_files()[0].parent / "uv.lock"
    worker_hashes = {}
    for name, entry in registry.workers.items():
        if entry.local_function:
            origin = util.find_spec(name)
            if origin is None or not origin.origin:
                raise NativeRuntimeError("Registered local kernel is missing")
            worker_hashes[name] = hashlib.sha256(
                Path(origin.origin).read_bytes()
            ).hexdigest()
            worker_hashes[name] = (
                worker_hashes[name],
                entry.local_function,
                entry.local_options,
            )
    with Path(sys.executable).open("rb") as source:
        executable_digest = hashlib.file_digest(source, "sha256").hexdigest()
    identity = {
        "executable": executable_digest,
        "uv_lock": hashlib.sha256(lock.read_bytes()).hexdigest(),
        "arch": machine(),
        "python": sys.version,
        "workers": worker_hashes,
        "packages": sorted(
            (d.metadata["Name"], d.version) for d in metadata.distributions()
        ),
    }
    return (
        "sha256:"
        + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    )


def _local_environment(scratch):
    return dict(
        HOME=str(scratch),
        TMPDIR=str(scratch),
        PATH=os.pathsep.join((str(Path(sys.prefix) / "bin"), "/usr/bin", "/bin")),
        PYTHONDONTWRITEBYTECODE="1",
        OPENBLAS_NUM_THREADS="1",
        OMP_NUM_THREADS="1",
        VECLIB_MAXIMUM_THREADS="1",
        MKL_NUM_THREADS="1",
        MPLCONFIGDIR=str(scratch),
    )


def _deny_sockets():
    """Irreversibly deny socket/async networking in a trusted Linux subprocess tree."""
    import ctypes
    import errno

    if sys.platform != "linux":
        raise NativeRuntimeError("Isolated subprocess requires Linux syscall filtering")
    try:
        seccomp = ctypes.CDLL("libseccomp.so.2")
    except OSError as error:
        raise NativeRuntimeError(
            "Linux network syscall filtering unavailable"
        ) from error
    seccomp.seccomp_init.argtypes = [ctypes.c_uint32]
    seccomp.seccomp_init.restype = ctypes.c_void_p
    seccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    seccomp.seccomp_rule_add.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    seccomp.seccomp_load.argtypes = [ctypes.c_void_p]
    seccomp.seccomp_release.argtypes = [ctypes.c_void_p]
    context = seccomp.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    if not context:
        raise NativeRuntimeError("Linux network filter initialization failed")
    try:
        for call in (
            b"socket",
            b"socketpair",
            b"connect",
            b"bind",
            b"io_uring_setup",
            b"pidfd_getfd",
            b"ptrace",
        ):
            number = seccomp.seccomp_syscall_resolve_name(call)
            if (
                number < 0
                or seccomp.seccomp_rule_add(
                    context, 0x00050000 | errno.EPERM, number, 0
                )
                != 0
            ):
                raise NativeRuntimeError("Linux network filter rule unavailable")
        if seccomp.seccomp_load(context) != 0:
            raise NativeRuntimeError("Linux network filter unavailable")
    finally:
        seccomp.seccomp_release(context)


def _local_entry():
    """A child receiving EOF exits before its recorded launch."""
    import importlib
    import resource
    import signal

    backend, module, function, inputs, outputs, request, limits, options = sys.argv[1:]
    profile = ResourceProfile(**json.loads(limits))
    if os.read(0, 1) != b"1":
        raise SystemExit(1)
    if backend == "isolated_subprocess":
        _deny_sockets()
    signal.alarm(profile.timeout_sec)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if sys.platform == "linux":
        allowed = sorted(os.sched_getaffinity(0))
        os.sched_setaffinity(0, allowed[: max(1, int(profile.cpus))])
    handler = getattr(importlib.import_module(module), function)
    handler(Path(inputs), Path(outputs), request, **json.loads(options))


def _landlock_command(stage, scratch):
    tool = shutil.which("setpriv")
    if sys.platform != "linux" or not tool:
        raise NativeRuntimeError("Linux filesystem isolation is unavailable")
    command = [tool, "--no-new-privs", "--landlock-access", "fs"]
    library_dirs = (
        Path(path) for path in ("/usr/bin", "/usr/lib", "/lib", "/lib64", "/bin")
    )
    rules = (
        *(
            (path, "execute,read-file,read-dir")
            for path in (*_local_files(), *library_dirs)
        ),
        (stage / "inputs", "read-file,read-dir"),
        (Path("/etc/ld.so.cache"), "read-file"),
        (Path("/etc/localtime"), "read-file"),
        (Path("/dev/urandom"), "read-file"),
        (Path("/dev/null"), "read-file,write-file"),
    )
    for path, rights in rules:
        if path.exists():
            command += ["--landlock-rule", f"path-beneath:{rights}:{path}"]
    writable = (
        "read-dir,read-file,write-file,remove-dir,remove-file,"
        "make-dir,make-reg,refer,truncate"
    )
    for path in (stage / "outputs", scratch):
        command += ["--landlock-rule", f"path-beneath:{writable}:{path}"]
    return [*command, "--"]


def _local_sandbox(name, stage, scratch, profile):
    if name == "isolated_subprocess":
        return _landlock_command(stage, scratch)
    runtime_dirs = tuple(dict.fromkeys(_local_files()))
    if name == "sandbox-exec":
        if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").is_file():
            raise NativeRuntimeError("macOS Seatbelt is unavailable")
        system_dirs = (
            "/usr/bin",
            "/usr/lib",
            "/usr/share",
            "/bin",
            "/System/Library",
            "/dev/null",
            "/dev/urandom",
            "/dev/random",
            "/dev/fd",
        )
        readable = (
            *system_dirs,
            *runtime_dirs,
            stage / "inputs",
            stage / "outputs",
            scratch,
        )

        def subpaths(paths):
            return " ".join(f"(subpath {json.dumps(str(path))})" for path in paths)

        policy = (
            "(version 1)(allow default)(deny network*)"
            '(deny file-read*)(deny file-write*)(allow file-read* (literal "/"))'
            "(allow file-read-metadata)"
            "(allow file-read* " + subpaths(readable) + ")"
            '(allow file-write* (literal "/dev/null") '
            + subpaths((scratch, stage / "outputs"))
            + ")"
        )
        return ["/usr/bin/sandbox-exec", "-p", policy]
    tool = shutil.which("bwrap")
    if sys.platform != "linux" or not tool:
        raise NativeRuntimeError("Bubblewrap unavailable")
    args = [
        tool,
        "--unshare-all",
        "--die-with-parent",
        "--cap-drop",
        "ALL",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
    ]
    for path in (*map(Path, ("/usr", "/bin", "/lib", "/lib64")), *runtime_dirs):
        if path.is_dir():
            args += ["--ro-bind", str(path), str(path)]
    return [
        *args,
        "--ro-bind",
        str(stage / "inputs"),
        str(stage / "inputs"),
        "--bind",
        str(stage / "outputs"),
        str(stage / "outputs"),
        "--size",
        str(profile.scratch_mb * 1024**2),
        "--tmpfs",
        str(scratch),
        "--chdir",
        str(scratch),
        "--",
    ]


def _scratch_size(scratch, limit):
    total = 0
    for _directory, _dirs, files, fd in os.fwalk(scratch):
        for name in files:
            try:
                total += os.stat(name, dir_fd=fd, follow_symlinks=False).st_size
            except FileNotFoundError:
                continue
            if total > limit:
                return total
    return total


def _local_process_exit(process, scratch, profile, *, exit_record=None):
    import time

    import psutil

    root = psutil.Process(process.pid)
    deadline = time.monotonic() + profile.timeout_sec
    while process.poll() is None:
        if exit_record is not None and exit_record.exists():
            return int(exit_record.read_text())
        if time.monotonic() >= deadline:
            raise NativeRuntimeError("Local worker resource ceiling exceeded")
        try:
            tree = [root, *root.children(recursive=True)]
            rss = sum(p.memory_info().rss for p in tree)
            tasks = sum(p.num_threads() for p in tree)
        except psutil.NoSuchProcess:
            continue
        scratch_limit = profile.scratch_mb * 1024**2
        scratch_size = _scratch_size(scratch, scratch_limit)
        if (
            rss > profile.memory_mb * 1024**2
            or tasks > profile.pids_limit
            or scratch_size > scratch_limit
        ):
            raise NativeRuntimeError("Local worker resource ceiling exceeded")
        time.sleep(0.05)
    return process.returncode


def _stop_local(created):
    """Only signal a new-session leader whose birth identity matches the journal."""
    import signal
    import time

    import psutil

    pid, birth = created["pid"], created["process_birth"]
    if type(pid) is not int or pid < 2 or type(birth) not in (int, float) or birth <= 0:
        raise NativeRuntimeError("Local worker process identity unverified")
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return
    try:
        leader = psutil.Process(pid)
        if (
            leader.create_time() != birth
            or os.getpgid(pid) != pid
            or os.getsid(pid) != pid
        ):
            raise NativeRuntimeError("Local worker process identity changed")
        os.killpg(pid, signal.SIGKILL)
        leader.wait(timeout=3)
    except (psutil.NoSuchProcess, ProcessLookupError):
        pass
    except psutil.TimeoutExpired as error:
        raise NativeRuntimeError("Local worker group cleanup unverified") from error
    # A killed descendant can remain in the group briefly while its new
    # parent reaps it. Never signal the numeric ID again after leader exit.
    until = time.monotonic() + 3
    while time.monotonic() < until:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.02)
    raise NativeRuntimeError("Local worker group cleanup unverified")


class NonOciPublicFallbackBackend:
    """Opt-in local, network-fenced execution of registered public/synthetic kernels."""

    def __init__(
        self,
        *,
        allow_non_oci_public_fallback=False,
        isolation_backend=None,
        registry=BUILTIN_WORKERS,
    ):
        self.enabled = allow_non_oci_public_fallback is True
        self.registry = registry
        self.name = isolation_backend or (
            "sandbox-exec"
            if sys.platform == "darwin"
            else "bwrap"
            if shutil.which("bwrap")
            else "isolated_subprocess"
        )
        if self.name not in {"bwrap", "sandbox-exec", "isolated_subprocess"}:
            raise ValueError("Unknown non-OCI fallback")

    @property
    def stamps(self):
        return {
            "isolation_backend": self.name,
            "oci_isolated": False,
            "max_claim_tier": "L1_execution_verified",
            "clinical_release_allowed": False,
        }

    def _policy(self, classification, claim_tier, purpose):
        if not self.enabled or classification not in {"synthetic", "public"}:
            raise PermissionError(
                "Local fallback requires opt-in and public or synthetic inputs"
            )
        if claim_tier not in {"none", "L1_execution_verified"} or purpose not in {
            "qc_diagnostic",
            "exploratory",
            "sensitivity_probe",
            "method_benchmark",
            "E1",
            "E2",
            "E3",
            "E4",
        }:
            raise PermissionError(
                "Local fallback cannot admit a confirmatory or elevated claim"
            )
        if os.geteuid() == 0:
            raise NativeRuntimeError("Local fallback requires an unprivileged worker")

    def probe(self):
        result = {**self.stamps, "available": False, "enabled": self.enabled}
        if not self.enabled or os.geteuid() == 0:
            return result
        try:
            with tempfile.TemporaryDirectory() as area:
                stage = Path(area).resolve()
                (stage / "inputs").mkdir()
                (stage / "outputs").mkdir()
                scratch = stage / "scratch"
                scratch.mkdir()
                args = _local_sandbox(self.name, stage, scratch, ResourceProfile())
                command = (
                    ["/usr/bin/true"]
                    if self.name != "isolated_subprocess"
                    else [
                        sys.executable,
                        "-I",
                        "-c",
                        "from devbench.engine.container_backend import _deny_sockets;"
                        "_deny_sockets()",
                    ]
                )
                subprocess.run(
                    [*args, *command],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=8,
                    check=True,
                    env=_local_environment(scratch),
                )
            return {
                **result,
                "available": True,
                "runtime_id": _local_pin(self.registry),
            }
        except (OSError, ValueError, subprocess.SubprocessError):
            return result

    def verify_image(self, image_id):
        if not self.enabled or image_id != _local_pin(self.registry):
            raise NativeRuntimeError(
                "Locked local worker environment differs from the pin"
            )
        return sys.platform + "/" + os.uname().machine

    def _launch(self, stage, intent, profile, worker):
        from dataclasses import asdict

        import psutil

        from .mounted_kernel_oci import _CREATED
        from .tier_a_executor import _write_record

        with tempfile.TemporaryDirectory(
            dir=stage, prefix=".local-scratch-"
        ) as temporary:
            scratch = Path(temporary)
            args = _local_sandbox(self.name, stage, scratch, profile)
            args.extend(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    "from devbench.engine.container_backend import _local_entry;"
                    "_local_entry()",
                    self.name,
                    worker.module,
                    worker.local_function,
                    str(stage / "inputs"),
                    str(stage / "outputs"),
                    intent["request_sha256"],
                    json.dumps(asdict(profile)),
                    json.dumps(dict(worker.local_options)),
                ]
            )
            with subprocess.Popen(
                args,
                cwd=scratch,
                env=_local_environment(scratch),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            ) as process:
                created = {
                    **intent,
                    "pid": process.pid,
                    "process_birth": psutil.Process(process.pid).create_time(),
                }
                try:
                    _write_record(stage.parent / _CREATED, created)
                    process.stdin.write(b"1")
                    process.stdin.close()
                    code = _local_process_exit(process, scratch, profile)
                except (BrokenPipeError, NativeRuntimeError):
                    code = 1
                finally:
                    _stop_local(created)
                return created, code

    def run_isolated(
        self,
        stage,
        *,
        request_sha,
        image_id,
        attempt_id,
        worker_module,
        resource_profile,
        data_classification,
        claim_tier,
        epistemic_purpose,
    ):
        from dataclasses import asdict

        from .mounted_kernel_oci import (
            _INTENT,
            _RECOVERED,
            _check_stage_location,
            _identity,
            _oci_lock,
            _optional_record,
            _prepare_mounts,
        )
        from .tier_a_executor import _write_record

        self._policy(data_classification, claim_tier, epistemic_purpose)
        _check_stage_location(stage)
        worker = self.registry.admit(
            worker_module, resource_profile, image_id, local=True
        )
        if resource_profile.gpu:
            raise NativeRuntimeError("Native GPU isolation is unavailable")
        platform = self.verify_image(image_id)
        binding = _identity(request_sha, image_id, attempt_id)
        with _oci_lock(stage.parent):
            if any(
                _optional_record(stage.parent, key) is not None
                for key in (_INTENT, _RECOVERED)
            ):
                raise NativeRuntimeError(
                    "Local worker has already launched or was disarmed"
                )
            _prepare_mounts(stage)
            intent = dict(
                binding,
                **self.stamps,
                worker_module=worker_module,
                resource_profile=asdict(resource_profile),
            )
            _write_record(stage.parent / _INTENT, intent)
            created, code = self._launch(stage, intent, resource_profile, worker)
            self.verify_image(image_id)
            proof = {
                **created,
                "claim_tier": "none" if code else "L1_execution_verified",
                "disposition": "exited",
                "cleanup_confirmed": True,
            }
            _write_record(stage.parent / _RECOVERED, proof)
        return {
            **proof,
            "platform": platform,
            "error_type": "LocalWorkerError" if code else None,
        }

    def cleanup(self, stage, *, request_sha, image_id, attempt_id):
        return _cleanup_local(stage, request_sha, image_id, attempt_id, self.stamps)


def _cleanup_local(stage, request_sha, image_id, attempt_id, stamps, *, gated=False):
    from .mounted_kernel_oci import (
        _CREATED,
        _INTENT,
        _RECOVERED,
        _check_stage_location,
        _identity,
        _oci_lock,
        _optional_record,
    )
    from .tier_a_executor import _write_record

    _check_stage_location(stage)
    binding = {**_identity(request_sha, image_id, attempt_id), **stamps}
    with _oci_lock(stage.parent):
        intent = _optional_record(stage.parent, _INTENT)
        created = _optional_record(stage.parent, _CREATED)
        proof = _optional_record(stage.parent, _RECOVERED)
        if intent and any(intent.get(k) != v for k, v in binding.items()):
            raise NativeRuntimeError("Local worker launch identity unverified")
        if created is None and (intent is None or gated):
            record = {
                **binding,
                "claim_tier": "none",
                "disposition": "not_created",
                "cleanup_confirmed": True,
            }
            if gated:
                record["oci_isolated"] = False
        else:
            if (
                not intent
                or not created
                or (
                    created
                    != {
                        **intent,
                        "pid": created.get("pid"),
                        "process_birth": created.get("process_birth"),
                    }
                )
            ):
                raise NativeRuntimeError("Local worker launch identity unverified")
            if proof is None:
                _stop_local(created)
            record = {
                **created,
                "claim_tier": "none",
                "disposition": "terminated",
                "cleanup_confirmed": True,
            }
            if (
                proof
                and proof.get("disposition") == "exited"
                and proof.get("claim_tier") in {"none", "L1_execution_verified"}
            ):
                record.update(disposition="exited", claim_tier=proof["claim_tier"])
                if (
                    gated
                    and proof["claim_tier"] == "none"
                    and not proof["oci_isolated"]
                ):
                    record.update(
                        isolation_backend="apptainer_no_netns", oci_isolated=False
                    )
        if proof is not None and proof != record:
            raise NativeRuntimeError("Local worker cleanup proof mismatch")
        if proof is None:
            _write_record(stage.parent / _RECOVERED, record)
        return record


# This code executes *inside* the requested SIF, before importing the worker.
# Verify effective mounts and the actual namespace for this invocation. A
# successful discovery probe on a different mount namespace grants no access.
_APPTAINER_PREFLIGHT = """
import json, os, socket, sys
host_ns, bindings, worker, request, limit = json.loads(sys.argv[1])
def require(condition):
    if not condition:
        raise SystemExit(74)
def inode(path):
    st = os.stat(path)
    return [st.st_dev, st.st_ino]
require(inode('/proc/self/ns/net') != host_ns)
require({name for _, name in socket.if_nameindex()} <= {'lo'})
with open('/proc/self/mountinfo') as source:
    mounts = [line.split()[4] for line in source]
for mount, identity in bindings.items():
    require(inode(mount) == identity and mounts.count(mount) == 1)
    require(not any(p.startswith(mount + '/') for p in mounts))
    require(bool(os.statvfs(mount).f_flag & os.ST_RDONLY) == (mount == '/inputs'))
if worker is None:
    with open('/outputs/probe', 'x') as out:
        out.write(request)
else:
    with open('/tmp/.devbench-isolation', 'x') as out:
        out.write(request)
    os.environ['DEVBENCH_MOUNTED_TIMEOUT_SEC'] = str(limit)
    os.execvp('python3.13', ['python3.13', '-m', worker, request])
"""

_APPTAINER_STAMPS = {
    "isolation_backend": "apptainer",
    "oci_isolated": True,
    "clinical_release_allowed": False,
}


def _apptainer_environment(home="/"):
    # Never inherit APPTAINER_BIND*, SINGULARITYENV_*, host credentials, or
    # site modules that redirect the engine to a different image/namespace.
    return {"HOME": str(home), "PATH": os.defpath, "LANG": "C", "LC_ALL": "C"}


def _apptainer_binary():
    if sys.platform != "linux" or os.geteuid() == 0:
        raise NativeRuntimeError("Apptainer needs an unprivileged Linux host")
    for binary in ("apptainer", "singularity"):
        path = shutil.which(binary)
        if path is None:
            continue
        executable = str(Path(path).resolve(strict=True))
        result = subprocess.run(
            [executable, "--version"],
            env=_apptainer_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
        if result.returncode == 0 and re.fullmatch(
            rb"(apptainer|singularity-ce) version [0-9]+\.[0-9]+[\w.+-]*",
            result.stdout.strip(),
            re.IGNORECASE,
        ):
            return executable
    raise NativeRuntimeError("Verified Apptainer or SingularityCE binary unavailable")


def _sif_source(path):
    """Walk every component with no-follow semantics, including parent dirs."""
    from contextlib import ExitStack

    if (
        not path.is_absolute()
        or path.suffix != ".sif"
        or ".." in path.parts
        or path.resolve(strict=True) != path
    ):
        raise NativeRuntimeError("A canonical local SIF image is required")
    with ExitStack() as stack:
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        stack.callback(os.close, fd)
        for name in path.parts[1:-1]:
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            stack.callback(os.close, fd)
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise NativeRuntimeError("SIF image must be a regular file")
        return os.fdopen(fd, "rb")


def _verified_sif(binding, destination=None):
    """Hash the bytes passed to exec, independent of later source path changes."""
    from contextlib import nullcontext

    path, expected = binding
    digest = hashlib.sha256()
    with _sif_source(path) as reader:
        # SIF global header: launch script (32), magic (10), version (3),
        # architecture (3). A renamed SquashFS or script is not a SIF.
        header = reader.read(48)
        arch = {b"02\x00": "amd64", b"04\x00": "arm64"}.get(header[45:48])
        if header[32:42] != b"SIF_MAGIC\x00" or arch is None:
            raise NativeRuntimeError("Invalid SIF header or unsupported architecture")
        digest.update(header)
        with destination.open("xb") if destination else nullcontext() as copy:
            if copy:
                copy.write(header)
            while block := reader.read(4 * 1024**2):
                digest.update(block)
                if copy:
                    copy.write(block)
            if copy:
                os.fchmod(copy.fileno(), 0o400)
    if digest.hexdigest() != expected:
        raise NativeRuntimeError("Apptainer SIF SHA-256 mismatch")
    return "linux/" + arch


def _configured_apptainer_map():
    value = json.loads(os.environ.get("DEVBENCH_APPTAINER_IMAGES", "{}"))
    if type(value) is not dict:
        raise NativeRuntimeError("Invalid Apptainer image map")
    return value


def _apptainer_selections():
    try:
        return _configured_apptainer_map()
    except (ValueError, RecursionError):
        # Without a parsed object there is no operator selection to override
        # the normal backend. Discovery still reports the HPC config failure.
        return {}


def configured_apptainer_ids() -> set[str]:
    """Image pins explicitly routed to HPC, including invalid SIF bindings."""
    return {
        pin
        for pin in _apptainer_selections()
        if re.fullmatch(r"sha256:[a-f0-9]{64}", pin)
    }


def _apptainer_images(config):
    if type(config) is not dict or len(config) > 64:
        raise NativeRuntimeError("Invalid Apptainer image map")
    images = {}
    for image_id, item in config.items():
        if (
            not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id)
            or type(item) is not dict
            or set(item) != {"path", "sha256"}
            or not isinstance(item["sha256"], str)
            or not re.fullmatch("[a-f0-9]{64}", item["sha256"])
            or not isinstance(item["path"], str)
        ):
            raise NativeRuntimeError("Invalid Apptainer SIF registration")
        path = Path(item["path"])
        if not path.is_absolute() or path.suffix != ".sif" or ".." in path.parts:
            raise NativeRuntimeError("A local absolute SIF path is required")
        images[image_id] = path, item["sha256"]
    return images


def _fs_identity(path):
    info = os.stat(path)
    return [info.st_dev, info.st_ino]


def _apptainer_command(
    executable, image, stage, scratch, worker, request, profile, gpu
):
    dirs = {"/inputs": stage / "inputs", "/outputs": stage / "outputs", "/tmp": scratch}
    if any(any(c in str(path) for c in ":,\n\r") for path in dirs.values()):
        raise NativeRuntimeError("Ambiguous Apptainer bind source")
    limits = (
        []
        if profile is None
        else [
            "--cpus",
            str(profile.cpus),
            "--memory",
            f"{profile.memory_mb}M",
            "--pids-limit",
            str(profile.pids_limit),
        ]
    )
    if profile and profile.gpu:
        limits.append("--nv" if gpu == "cuda" else "--rocm")
    expected = {mount: _fs_identity(path) for mount, path in dirs.items()}
    preflight = [
        _fs_identity("/proc/self/ns/net"),
        expected,
        worker,
        request,
        profile.timeout_sec if profile else 0,
    ]
    return [
        executable,
        "exec",
        "--containall",
        "--no-home",
        "--cleanenv",
        "--no-eval",
        "--net",
        "--network=none",
        "--no-mount",
        "cwd,hostfs,bind-paths",
        "--pwd",
        "/tmp",
        "--bind",
        f"{dirs['/inputs']}:/inputs:ro",
        "--bind",
        f"{dirs['/outputs']}:/outputs",
        "--bind",
        f"{scratch}:/tmp",
        *limits,
        str(image),
        "python3.13",
        "-c",
        _APPTAINER_PREFLIGHT,
        json.dumps(preflight),
    ]


def _apptainer_probe(executable, sif, area):
    import signal
    from uuid import uuid4

    with tempfile.TemporaryDirectory(dir=area, prefix="probe-") as temp:
        stage = Path(temp)
        scratch = stage / "scratch"
        for path in (stage / "inputs", stage / "outputs", scratch):
            path.mkdir(mode=0o700)
        nonce = uuid4().hex
        command = _apptainer_command(
            executable, sif, stage, scratch, None, nonce, None, None
        )
        with subprocess.Popen(
            command,
            cwd="/",
            env=_apptainer_environment(area),
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ) as process:
            try:
                if process.wait(timeout=8) != 0:
                    return False
            except subprocess.TimeoutExpired:
                # The unreaped, owned leader still reserves the group ID.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                return False
        try:
            with (stage / "outputs/probe").open("rb") as receipt:
                return receipt.read(33) == nonce.encode()
        except FileNotFoundError:
            return False


def _apptainer_launch(command, directory, intent, scratch, profile):
    import psutil

    from .mounted_kernel_oci import _CREATED
    from .tier_a_executor import _write_record

    # The group leader stays alive until teardown, even if the real engine
    # exits first. On parent loss its pipe closes and the whole group dies.
    # The guest cannot reach the host-only status file.
    gate = """
import os, select, signal, subprocess, sys
if os.read(0, 1) != b'1':
    sys.exit(1)
child = subprocess.Popen(sys.argv[2:], stdin=subprocess.DEVNULL)
while child.poll() is None:
    if select.select([0], [], [], 0.1)[0]:
        os.killpg(0, signal.SIGKILL)
with open(sys.argv[1] + '.tmp', 'x') as status:
    status.write(str(child.returncode))
os.rename(sys.argv[1] + '.tmp', sys.argv[1])
os.read(0, 1)
os.killpg(0, signal.SIGKILL)
"""
    exit_record = scratch.parent / "worker-exit"
    with subprocess.Popen(
        [sys.executable, "-I", "-S", "-c", gate, str(exit_record), *command],
        cwd="/",
        env=_apptainer_environment(scratch.parent),
        start_new_session=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) as process:
        created = {
            **intent,
            "pid": process.pid,
            "process_birth": psutil.Process(process.pid).create_time(),
        }
        try:
            _write_record(directory / _CREATED, created)
            process.stdin.write(b"1")
            process.stdin.flush()
            code = _local_process_exit(
                process, scratch, profile, exit_record=exit_record
            )
        except (NativeRuntimeError, BrokenPipeError):
            code = 1
        finally:
            _stop_local(created)
        return created, code


class ApptainerBackend:
    """HPC execution using an operator-mapped and independently hashed local SIF.

    DEVBENCH_APPTAINER_IMAGES is a daemon-side JSON object keyed by the frozen
    image ID. Each value has an absolute SIF 'path' and its own 'sha256'.
    A SIF conversion and its source OCI image have distinct content digests.
    """

    name = "apptainer"

    def __init__(self, *, images=None, registry=BUILTIN_WORKERS, gpu_backend=None):
        self.registry = registry
        self.gpu_backend = gpu_backend or os.environ.get(
            "DEVBENCH_APPTAINER_GPU", "cuda"
        )
        if self.gpu_backend not in {"cuda", "rocm"}:
            raise NativeRuntimeError("Unknown Apptainer GPU backend")
        if images is None:
            config = _configured_apptainer_map()
            if len(config) > 64:
                raise NativeRuntimeError("Invalid Apptainer image map")
            self.images = {}
            for pin, source in config.items():
                try:
                    self.images.update(_apptainer_images({pin: source}))
                except NativeRuntimeError:
                    # Discovery must still probe independently valid bindings.
                    continue
        else:
            self.images = _apptainer_images(images)

    @classmethod
    def configured_for(cls, image_id):
        config = _apptainer_selections()
        if image_id not in config:
            return None
        if len(config) > 64:
            raise NativeRuntimeError("Invalid Apptainer image map")
        # A named but invalid binding must fail here, before Docker selection.
        return cls(images={image_id: config[image_id]})

    def _binding(self, image_id):
        try:
            return self.images[image_id]
        except KeyError as error:
            raise NativeRuntimeError("No local SIF bound to this image pin") from error

    def probe(self):
        try:
            executable = _apptainer_binary()
        except (OSError, ValueError, subprocess.SubprocessError):
            return _capability(
                self.name, available=False, isolated=False, local_image_ids=[]
            )
        ready = []
        attempted = False
        for image_id, binding in self.images.items():
            try:
                with tempfile.TemporaryDirectory(prefix="sif-probe-") as temp:
                    area = Path(temp).resolve()
                    sif = area / "runtime.sif"
                    _verified_sif(binding, sif)
                    attempted = True
                    if _apptainer_probe(executable, sif, area):
                        ready.append(image_id)
            except (OSError, ValueError, subprocess.SubprocessError):
                continue
        return _capability(
            self.name if ready or not attempted else "apptainer_no_netns",
            available=True,
            isolated=bool(ready),
            local_image_ids=sorted(ready),
        )

    def verify_image(self, image_id):
        return _verified_sif(self._binding(image_id))

    def run_isolated(
        self,
        stage,
        *,
        request_sha,
        image_id,
        attempt_id,
        worker_module,
        resource_profile,
        data_classification,
        claim_tier,
        epistemic_purpose,
    ):
        from dataclasses import asdict

        from devbench.core._files import read_bounded_file

        from .mounted_kernel_oci import (
            _INTENT,
            _RECOVERED,
            _check_stage_location,
            _identity,
            _oci_lock,
            _optional_record,
            _prepare_mounts,
        )
        from .tier_a_executor import _write_record

        _check_stage_location(stage)
        self.registry.admit(worker_module, resource_profile, image_id)
        executable = _apptainer_binary()
        binding = self._binding(image_id)
        identity = _identity(request_sha, image_id, attempt_id)
        with _oci_lock(stage.parent):
            if any(
                _optional_record(stage.parent, k) is not None
                for k in (_INTENT, _RECOVERED)
            ):
                raise NativeRuntimeError(
                    "Apptainer worker already launched or disarmed"
                )
            area = stage / (".apptainer-" + attempt_id)
            area.mkdir(mode=0o700)
            try:
                sif = area / "runtime.sif"
                platform = _verified_sif(binding, sif)
                if not _apptainer_probe(executable, sif, area):
                    raise NativeRuntimeError(
                        "Apptainer private network or read-only mount unavailable"
                    )
                _prepare_mounts(stage)
                scratch = area / "scratch"
                scratch.mkdir(mode=0o700)
                command = _apptainer_command(
                    executable,
                    sif,
                    stage,
                    scratch,
                    worker_module,
                    request_sha,
                    resource_profile,
                    self.gpu_backend,
                )
                intent = dict(
                    identity,
                    **_APPTAINER_STAMPS,
                    worker_module=worker_module,
                    resource_profile=asdict(resource_profile),
                    sif_sha256=binding[1],
                )
                _write_record(stage.parent / _INTENT, intent)
                created, code = _apptainer_launch(
                    command, stage.parent, intent, scratch, resource_profile
                )
                try:
                    verified = (
                        read_bounded_file(scratch / ".devbench-isolation", 64)
                        == request_sha.encode()
                    )
                except (OSError, ValueError):
                    verified = False
                if not verified:
                    code = 1
                proof = {
                    **created,
                    "oci_isolated": verified,
                    "isolation_backend": self.name
                    if verified
                    else "apptainer_no_netns",
                    "claim_tier": "none" if code else "L1_execution_verified",
                    "disposition": "exited",
                    "cleanup_confirmed": True,
                }
                _write_record(stage.parent / _RECOVERED, proof)
            finally:
                shutil.rmtree(area)
        return {
            **proof,
            "platform": platform,
            "error_type": "ApptainerError" if code else None,
        }

    def cleanup(self, stage, *, request_sha, image_id, attempt_id):
        proof = _cleanup_local(
            stage, request_sha, image_id, attempt_id, _APPTAINER_STAMPS, gated=True
        )
        area = stage / (".apptainer-" + attempt_id)
        if area.exists():
            shutil.rmtree(area)
        return proof


def probe_container_backends(*, allow_non_oci_public_fallback=False):
    """Inspect live engines and the explicitly configured public fallback."""
    fallback = NonOciPublicFallbackBackend(
        allow_non_oci_public_fallback=allow_non_oci_public_fallback
    )
    results = {
        engine.name: engine.probe()
        for engine in (DockerBackend(), PodmanBackend(), fallback)
    }
    try:
        results["apptainer"] = ApptainerBackend().probe()
    except (ValueError, RecursionError):
        # Malformed HPC configuration must not invalidate other engines' probes.
        results["apptainer"] = _capability(
            "apptainer", available=False, isolated=False, local_image_ids=[]
        )
    return results
