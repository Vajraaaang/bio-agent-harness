"""Daemon-side transport and durable launch fences for registered mounted workers."""

import fcntl
import os
import re
import shutil
import stat
import sys
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import asdict, replace

from devharness_controlled.native_contracts import limma_voom_kernel_images
from devharness_controlled.native_runtime import NativeRuntimeError

from .container_backend import (
    ApptainerBackend,
    DockerBackend,
    LocalDocker,
    PodmanBackend,
    ResourceProfile,
    configured_apptainer_ids,
)

_LABEL = "io.devbench.mounted-request-sha256"
_INTENT = "oci-create-intent.json"
_CREATED = "oci-created.json"
_RECOVERED = "oci-cleanup.json"


def require_mounted_pipeline_runtime(
    spec,
    image_id,
    *,
    representation,
    allow_python_wald=False,
):
    """Apply the installed image policy at the application execution boundary."""
    spec.require_mounted_runtime(
        image_id,
        representation=representation,
        allow_python_wald=allow_python_wald,
        approved_voom_image_ids=limma_voom_kernel_images(),
    )
    if image_id is None:
        raise PermissionError("Mounted research inference requires a pinned OCI image")


@contextmanager
def _oci_lock(directory):
    fd = os.open(
        directory / "oci-worker.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    try:
        metadata = os.fstat(fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o077
        ):
            raise NativeRuntimeError("Mounted OCI lock identity unverified")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise NativeRuntimeError("Mounted OCI worker still active") from error
        yield
    finally:
        os.close(fd)


def _optional_record(directory, name):
    from .tier_a_executor import _read_record

    try:
        return _read_record(directory / name, 16_384)
    except FileNotFoundError:
        return None


def _identity(request_sha, image_id, attempt_id):
    if (
        not re.fullmatch(r"[a-f0-9]{64}", request_sha)
        or not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id)
        or not re.fullmatch(r"[a-f0-9]{32}", attempt_id)
    ):
        raise NativeRuntimeError("Invalid mounted OCI launch identity")
    return {
        "name": "devbench-rna-tier-a-" + attempt_id,
        "request_sha256": request_sha,
        "image_id": image_id,
    }


def _daemon(runtime):
    # Used by the existing daemon Nextflow recovery and paired-diagnostic driver.
    return DockerBackend().identity(runtime)


def _private_runtime(directory, **options):
    return DockerBackend(runtime_factory=LocalDocker).session(directory, **options)


def _container_ids(runtime, selector):
    candidates = runtime._call(
        ["container", "ls", "-aq", "--no-trunc", "--filter", selector],
    ).splitlines()
    if len(candidates) > 1 or any(
        not re.fullmatch(r"[a-f0-9]{64}", value) for value in candidates
    ):
        raise NativeRuntimeError("Mounted kernel container inventory unverified")
    return candidates


def _clean(runtime, stage, intent):
    from .tier_a_executor import _write_record

    directory = stage.parent
    created = _optional_record(directory, _CREATED)
    selector = f"name=^/{intent['name']}$"
    ids = _container_ids(runtime, selector)
    if not ids:
        if created is None:
            # A killed Docker client can leave a daemon request in flight.
            raise NativeRuntimeError("Mounted OCI create is uncertain")
        container = created.get("container_id")
        if not isinstance(container, str) or not re.fullmatch(
            r"[a-f0-9]{64}", container
        ):
            raise NativeRuntimeError(
                "Mounted OCI retained container identity unverified"
            )
        if created != {**intent, "container_id": container}:
            raise NativeRuntimeError("Mounted OCI retained create record mismatch")
        if _container_ids(runtime, f"id={container}"):
            raise NativeRuntimeError("Mounted OCI retained container name changed")
        return {**created, "disposition": "already_removed", "cleanup_confirmed": True}
    container = ids[0]
    if created is not None and created != {**intent, "container_id": container}:
        raise NativeRuntimeError("Mounted OCI retained create record mismatch")
    observed = _owned(
        runtime,
        intent["name"],
        intent["image_id"],
        intent["request_sha256"],
        container,
    )
    _verify_mounts(observed, stage)
    if created is None:
        created = {**intent, "container_id": container}
        _write_record(directory / _CREATED, created)
    if (
        runtime._call(["rm", "--force", container]) != container
        or _container_ids(runtime, f"id={container}")
        or _container_ids(runtime, selector)
    ):
        raise NativeRuntimeError("Mounted kernel container cleanup unverified")
    return {**created, "disposition": "removed", "cleanup_confirmed": True}


def _validate_cleanup_record(directory, binding, proof, stamps=None):
    intent = _optional_record(directory, _INTENT)
    created = _optional_record(directory, _CREATED)
    disposition = proof.get("disposition")
    if disposition == "not_created":
        valid = (
            intent is None
            and created is None
            and proof
            in (
                {**binding, "disposition": disposition, "cleanup_confirmed": True},
                {
                    **binding,
                    **(stamps or {}),
                    "disposition": disposition,
                    "cleanup_confirmed": True,
                },
            )
        )
    else:
        valid = (
            disposition in {"removed", "already_removed"}
            and intent is not None
            and created is not None
            and all(intent.get(key) == value for key, value in binding.items())
            and bool(re.fullmatch(r"[a-f0-9]{64}", str(created.get("container_id"))))
            and created == {**intent, "container_id": created.get("container_id")}
            and proof
            == {**created, "disposition": disposition, "cleanup_confirmed": True}
        )
    if not valid:
        raise NativeRuntimeError("Mounted OCI cleanup record mismatch")
    return proof


def _stamps(backend):
    return {
        "isolation_backend": backend.name,
        "oci_isolated": True,
        "clinical_release_allowed": False,
    }


def _check_retained_backend(backend, runtime, binding, intent):
    identity = backend.identity(runtime)
    if "isolation_backend" not in intent:
        # Sealed pre-ADR-20 intents exist in historical Docker attempts.
        expected = {**binding, **identity}
        matches = backend.name == "docker" and intent == expected
    else:
        expected = {**binding, **identity, **_stamps(backend)}
        matches = set(intent) == expected.keys() | {
            "resource_profile",
            "worker_module",
        } and all(intent[key] == value for key, value in expected.items())
    if not matches:
        raise NativeRuntimeError(
            "Mounted OCI recovery daemon mismatch or Podman storage mismatch"
        )


def _recover_oci(backend, stage, request_sha, image_id, attempt_id):
    from .tier_a_executor import _write_record

    _check_stage_location(stage)
    binding = _identity(request_sha, image_id, attempt_id)
    directory = stage.parent
    with _oci_lock(directory):
        proof = _optional_record(directory, _RECOVERED)
        if proof is not None:
            return _validate_cleanup_record(directory, binding, proof, _stamps(backend))
        intent = _optional_record(directory, _INTENT)
        if intent is None:
            if _optional_record(directory, _CREATED) is not None:
                raise NativeRuntimeError("Missing mounted OCI create intent")
            proof = {
                **binding,
                **_stamps(backend),
                "disposition": "not_created",
                "cleanup_confirmed": True,
            }
        else:
            if (
                any(intent.get(key) != value for key, value in binding.items())
                or not isinstance(intent.get("endpoint"), str)
                or intent.get("isolation_backend", "docker") != backend.name
            ):
                raise NativeRuntimeError("Mounted OCI launch record mismatch")
            with backend.session(stage, endpoint=intent["endpoint"]) as runtime:
                _check_retained_backend(backend, runtime, binding, intent)
                proof = _clean(runtime, stage, intent)
                _check_retained_backend(backend, runtime, binding, intent)
        _write_record(directory / _RECOVERED, proof)
        return proof


def _configuration(runtime, image_id):
    return DockerBackend().image_platform(runtime, image_id)


def _owned(runtime, name, image_id, request_sha, identity):
    document = runtime.inspect(identity or name)
    actual = document["Id"]
    if (
        document["Name"] != "/" + name
        or document["Image"] != image_id
        or document["Config"].get("Labels", {}).get(_LABEL) != request_sha
        or type(actual) is not str
        or not re.fullmatch(r"[a-f0-9]{64}", actual)
        or identity is not None
        and actual != identity
    ):
        raise NativeRuntimeError("Mounted kernel container ownership unverified")
    return document


def _allow_worker_read(fd, name, item):
    if item.st_nlink != 1:
        raise NativeRuntimeError("Linked input is not readable by the worker")
    descriptor = os.open(name, os.O_NONBLOCK | os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
    try:
        linked = os.fstat(descriptor)
        if linked.st_nlink != 1 or (linked.st_dev, linked.st_ino) != (
            item.st_dev,
            item.st_ino,
        ):
            raise NativeRuntimeError("Mounted input changed during admission")
        os.fchmod(descriptor, 0o444)
    finally:
        os.close(descriptor)


def _check_stage(stage):
    for name in ("inputs", "outputs"):
        path = stage / name
        if (
            path.resolve(strict=True) != path
            or not path.is_dir()
            or any(ch in str(path) for ch in (",", "\r", "\n"))
        ):
            raise NativeRuntimeError("Invalid mounted kernel stage")
    if any((stage / "outputs").iterdir()):
        raise NativeRuntimeError("Mounted kernel requires empty output staging")


def _prepare_mounts(stage):
    # Containers receive directory inventories, never arbitrary caller bind paths.
    # In particular never chmod a large attested source through its staging hardlink.
    _check_stage(stage)
    root = os.geteuid() == 0
    uid, gid = (65532, 65532) if root else (os.geteuid(), os.getegid())
    for _directory, dirs, files, fd in os.fwalk(stage / "inputs"):
        if root:
            os.fchmod(fd, 0o555)
        for name in dirs + files:
            item = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(item.st_mode):
                continue
            if not stat.S_ISREG(item.st_mode):
                raise NativeRuntimeError("Mounted kernel inputs must be regular files")
            if root and not item.st_mode & stat.S_IROTH:
                _allow_worker_read(fd, name, item)
    if root:
        os.chown(stage / "outputs", uid, gid, follow_symlinks=False)
    return uid, gid


def _mount_arguments(
    stage,
    image,
    name,
    request_sha,
    timeout,
    memory_mb,
    platform,
    *,
    worker_module="rna_kernels.mounted_bulk",
    resource_profile=None,
    nonrecursive="bind-recursive=disabled",
    backend_options=(),
):
    profile = resource_profile or ResourceProfile(
        memory_mb=memory_mb, timeout_sec=timeout
    )
    uid, gid = _prepare_mounts(stage)
    # Supply the inner R watchdog from the verified command. The outer timeout
    # bounds the whole process tree, including setup and result validation.
    solver_env = (
        ["/usr/bin/env", f"DEVBENCH_MOUNTED_TIMEOUT_SEC={profile.timeout_sec}"]
        if worker_module == "rna_kernels.mounted_bulk"
        else []
    )
    return [
        "create",
        "--name",
        name,
        "--pull=never",
        "--no-healthcheck",
        *backend_options,
        "--platform",
        platform,
        "--network",
        "none",
        "--read-only",
        "--security-opt",
        "no-new-privileges:true",
        "--cap-drop",
        "ALL",
        "--user",
        f"{uid}:{gid}",
        "--pids-limit",
        str(profile.pids_limit),
        "--memory",
        f"{profile.memory_mb}m",
        "--memory-swap",
        f"{profile.memory_mb}m",
        "--cpus",
        str(profile.cpus),
        "--log-driver",
        "none",
        "--ipc",
        "private",
        "--tmpfs",
        f"/tmp:rw,nosuid,nodev,noexec,size={profile.scratch_mb * 1024**2},mode=1777",
        "--label",
        f"{_LABEL}={request_sha}",
        "--mount",
        f"type=bind,src={stage / 'inputs'},dst=/inputs,readonly,{nonrecursive}",
        "--mount",
        f"type=bind,src={stage / 'outputs'},dst=/outputs,{nonrecursive}",
        "--entrypoint",
        "/usr/bin/timeout",
        image,
        "--signal=KILL",
        str(profile.timeout_sec),
        *solver_env,
        "python3.13",
        "-m",
        worker_module,
        request_sha,
    ]


def _verify_mounts(document, stage):
    mounts = document.get("Mounts", [])
    expected = {
        (str(stage / "inputs"), "/inputs", False),
        (str(stage / "outputs"), "/outputs", True),
    }
    if (
        len(mounts) != 2
        or any(mount["Type"] != "bind" for mount in mounts)
        or {(mount["Source"], mount["Destination"], mount["RW"]) for mount in mounts}
        != expected
    ):
        raise NativeRuntimeError(
            "Mounted kernel bind inventory differs from the private stage"
        )
    host = document["HostConfig"]
    if (
        host.get("NetworkMode") != "none"
        or host.get("ReadonlyRootfs") is not True
        or host.get("Privileged") is not False
    ):
        raise NativeRuntimeError("Mounted kernel isolation unverified")


def _scratch_matches(tmpfs, size_mb):
    if not isinstance(tmpfs, dict) or set(tmpfs) != {"/tmp"}:
        return False
    if not isinstance(tmpfs["/tmp"], str):
        return False
    flags = tmpfs["/tmp"].split(",")
    options = {option.partition("=")[0]: option.partition("=")[2] for option in flags}
    size = re.fullmatch(r"(\d+)([kmg]?)", options.get("size", "").lower())
    if len(options) != len(flags) or size is None:
        return False
    multiplier = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[size[2]]
    return (
        int(size[1]) * multiplier == size_mb * 1024**2
        and options.get("mode") in {"1777", "01777"}
        and {"rw", "nosuid", "nodev", "noexec"} <= set(flags)
        and not {"ro", "suid", "dev", "exec"} & set(flags)
    )


def _private_capabilities(document, engine):
    host = document["HostConfig"]
    if host.get("CapAdd", ["missing"]) not in (None, []):
        return False
    security = host.get("SecurityOpt")
    if security not in (
        ["no-new-privileges"],
        ["no-new-privileges:true"],
        ["no-new-privileges=true"],
    ):
        return False
    if engine == "docker":
        return host.get("CapDrop") == ["ALL"]
    # Podman enumerates the individual default capabilities removed by cap-drop
    # ALL. Its actual OCI bounding/effective sets are available directly.
    return all(
        document.get(field, ["missing"]) in (None, [])
        for field in ("EffectiveCaps", "BoundingCaps")
    )


def _keeps_podman_ids(host, uid, gid):
    # Podman's private user namespace maps its process UID/GID to ID 0 of
    # the *outer rootless namespace*, which is the host user. Older versions
    # report UsernsMode=private and expose the keep-id mapping separately.
    mapping = host.get("IDMappings")
    return (
        host.get("UsernsMode") in ("private", "keep-id")
        and isinstance(mapping, dict)
        and isinstance(mapping.get("UidMap"), list)
        and isinstance(mapping.get("GidMap"), list)
        and mapping["UidMap"].count(f"{uid}:0:1") == 1
        and mapping["GidMap"].count(f"{gid}:0:1") == 1
    )


def _nonrecursive_mounts(document, engine):
    actual = document["Mounts"]
    if engine == "podman":
        return all(
            isinstance(mount.get("Options"), list)
            and "bind" in mount["Options"]
            and "rbind" not in mount["Options"]
            for mount in actual
        )
    configured = document["HostConfig"].get("Mounts")
    if not isinstance(configured, list) or len(configured) != 2:
        return False
    expected = {(mount["Source"], mount["Destination"]) for mount in actual}
    return (
        all(
            isinstance(mount, dict)
            and mount.get("Type") == "bind"
            and (mount.get("BindOptions") or {}).get("NonRecursive") is True
            for mount in configured
        )
        and {(mount.get("Source"), mount.get("Target")) for mount in configured}
        == expected
    )


def _devices_match(document, engine, gpu):
    host = document["HostConfig"]
    if engine == "docker":
        if host.get("Devices", ["missing"]) not in (None, []):
            return False
        requests = host.get("DeviceRequests", ["missing"])
        if not gpu:
            return requests in (None, [])
        return (
            isinstance(requests, list)
            and len(requests) == 1
            and isinstance(requests[0], dict)
            and requests[0].get("Count") == -1
            and requests[0].get("Driver") in ("", "nvidia")
            and requests[0].get("Capabilities") == [["gpu"]]
            and requests[0].get("DeviceIDs") in (None, [])
            and not requests[0].get("Options")
        )
    devices = host.get("Devices", ["missing"])
    if not gpu:
        return devices in (None, [])
    # CDI on Podman resolves device node mappings before production use.
    return (
        isinstance(devices, list)
        and bool(devices)
        and all(
            isinstance(device, dict)
            and isinstance(device.get("PathInContainer"), str)
            and re.fullmatch(
                r"/dev/nvidia(?:[0-9]+|ctl|[-/][a-z0-9/-]+)", device["PathInContainer"]
            )
            and device.get("PathOnHost") == device["PathInContainer"]
            for device in devices
        )
    )


def _verify_launch_config(document, profile, command, engine):
    config, host = document["Config"], document["HostConfig"]
    uid, gid = (os.geteuid(), os.getegid()) if os.geteuid() else (65532, 65532)
    memory = profile.memory_mb * 1024**2
    expected = {
        "Memory": memory,
        "MemorySwap": memory,
        "PidsLimit": profile.pids_limit,
        "NanoCpus": round(profile.cpus * 1_000_000_000),
    }
    if expected["NanoCpus"] < 1 or any(
        type(host.get(key)) is not int or host[key] != val
        for key, val in expected.items()
    ):
        raise NativeRuntimeError(
            "Mounted worker resource limits differ from its profile"
        )
    if (
        config.get("User") != f"{uid}:{gid}"
        or config.get("Entrypoint") != ["/usr/bin/timeout"]
        or config.get("Cmd") != command
        or host.get("PidMode") not in ("", "private")
        or host.get("IpcMode") != "private"
        or (host.get("LogConfig") or {}).get("Type") != "none"
    ):
        raise NativeRuntimeError(
            "Mounted worker identity, namespace or command differs"
        )
    if not all(
        (
            _private_capabilities(document, engine),
            _scratch_matches(host.get("Tmpfs"), profile.scratch_mb),
            _devices_match(document, engine, profile.gpu),
            _nonrecursive_mounts(document, engine),
            engine != "podman" or _keeps_podman_ids(host, uid, gid),
        )
    ):
        raise NativeRuntimeError(
            "Mounted worker capability, device, scratch or user namespace differs"
        )


def _default_backend(image_id):
    apptainer = ApptainerBackend.configured_for(image_id)
    if apptainer is not None:
        return apptainer
    docker = DockerBackend(runtime_factory=LocalDocker)
    if (
        sys.platform == "linux"
        and shutil.which("podman")
        and not docker.probe()["available"]
    ):
        podman = PodmanBackend()
        if podman.probe()["oci_isolated"]:
            return podman
    return docker


def _check_stage_location(stage):
    # Recovery may replay a durable proof after the stage itself has been purged.
    if (
        not stage.is_absolute()
        or stage.resolve() != stage
        or stage.parent.resolve(strict=True) != stage.parent
    ):
        raise NativeRuntimeError("Mounted runtime requires a canonical stage path")


def recover_mounted_container(
    stage, *, request_sha, image_id, attempt_id, backend=None
):
    """Fence an orphan on its recorded engine, including historical Docker attempts."""
    _check_stage_location(stage)
    if backend is None:
        intent = (
            _optional_record(stage.parent, _INTENT)
            or _optional_record(stage.parent, _RECOVERED)
            or {}
        )
        name = intent.get("isolation_backend", "docker")
        if not intent and image_id in configured_apptainer_ids():
            # No guest exists yet. Fence the attempt on its selected backend
            # even if the registered SIF is now unavailable or invalid.
            name = "apptainer"
        if name == "docker":
            backend = DockerBackend(runtime_factory=LocalDocker)
        elif name == "podman":
            backend = PodmanBackend()
        elif name in {"apptainer", "apptainer_no_netns"}:
            backend = ApptainerBackend(images={}, gpu_backend="cuda")
        elif name in {"bwrap", "sandbox-exec", "isolated_subprocess"}:
            from .container_backend import NonOciPublicFallbackBackend

            # Cleanup can fence a recorded local process without admitting another run.
            backend = NonOciPublicFallbackBackend(isolation_backend=name)
        else:
            raise NativeRuntimeError("Unknown recorded mounted runtime")
    return backend.cleanup(
        stage, request_sha=request_sha, image_id=image_id, attempt_id=attempt_id
    )


def _resources(backend, module, profile, timeout, memory_mb):
    from .mounted_worker_registry import BUILTIN_WORKERS

    legacy = {
        key: limit
        for key, limit in (("timeout_sec", timeout), ("memory_mb", memory_mb))
        if limit is not None
    }
    if profile is None or isinstance(profile, Mapping):
        defaults = getattr(backend, "registry", BUILTIN_WORKERS).worker(module).defaults
        try:
            profile = replace(defaults, **(legacy if profile is None else profile))
        except TypeError as error:
            raise ValueError("Invalid declared worker resource profile") from error
    if not isinstance(profile, ResourceProfile):
        raise ValueError("A registered worker resource profile is required")
    if any(getattr(profile, key) != value for key, value in legacy.items()):
        raise ValueError("Conflicting legacy and declared worker limits")
    return profile


def run_mounted_container(
    stage,
    *,
    request_sha,
    image_id,
    attempt_id,
    timeout=None,
    memory_mb=None,
    worker_module="rna_kernels.mounted_bulk",
    resource_profile=None,
    backend=None,
    data_classification="unclassified",
    claim_tier="none",
    epistemic_purpose="exploratory",
):
    """Execute a registered worker with the daemon's selected isolation backend."""
    _check_stage_location(stage)
    if any(
        _optional_record(stage.parent, key) is not None for key in (_INTENT, _RECOVERED)
    ):
        raise NativeRuntimeError(
            "Mounted OCI worker has already launched or was disarmed"
        )
    selected = backend or _default_backend(image_id)
    resource_profile = _resources(
        selected, worker_module, resource_profile, timeout, memory_mb
    )
    return selected.run_isolated(
        stage,
        request_sha=request_sha,
        image_id=image_id,
        attempt_id=attempt_id,
        worker_module=worker_module,
        resource_profile=resource_profile,
        data_classification=data_classification,
        claim_tier=claim_tier,
        epistemic_purpose=epistemic_purpose,
    )


def _run_oci(backend, stage, request_sha, image_id, attempt_id, worker_module, profile):
    from .tier_a_executor import _write_record

    _check_stage_location(stage)
    binding = _identity(request_sha, image_id, attempt_id)
    directory = stage.parent
    with _oci_lock(directory):
        if any(
            _optional_record(directory, name) is not None
            for name in (_INTENT, _RECOVERED)
        ):
            raise NativeRuntimeError(
                "Mounted OCI worker has already launched or was disarmed"
            )
        with backend.session(stage) as runtime:
            platform = backend.image_platform(runtime, image_id)
            arguments = _mount_arguments(
                stage,
                image_id,
                binding["name"],
                request_sha,
                profile.timeout_sec,
                profile.memory_mb,
                platform,
                worker_module=worker_module,
                resource_profile=profile,
                nonrecursive=backend.nonrecursive,
                backend_options=backend.launch_options(runtime, profile),
            )
            intent = {
                **binding,
                **backend.identity(runtime),
                **_stamps(backend),
                "resource_profile": asdict(profile),
                "worker_module": worker_module,
            }
            _write_record(directory / _INTENT, intent)
            error = None
            try:
                container = runtime._call(arguments)
                if not re.fullmatch(r"[a-f0-9]{64}", container):
                    raise NativeRuntimeError(
                        "Docker returned an invalid mounted kernel container ID"
                    )
                _start_container(
                    runtime,
                    stage,
                    intent,
                    container,
                    profile,
                    arguments[arguments.index("--entrypoint") + 3 :],
                )
            except NativeRuntimeError as rejected:
                error = type(rejected).__name__
            _check_retained_backend(backend, runtime, binding, intent)
            proof = _clean(runtime, stage, intent)
            _check_retained_backend(backend, runtime, binding, intent)
            _write_record(directory / _RECOVERED, proof)
        return {**proof, "platform": platform, "error_type": error}


def _start_container(runtime, stage, intent, container, profile, command):
    from .tier_a_executor import _write_record

    def inspect():
        return _owned(
            runtime,
            intent["name"],
            intent["image_id"],
            intent["request_sha256"],
            container,
        )

    observed = inspect()
    _verify_mounts(observed, stage)
    _verify_launch_config(
        observed, profile, command, intent.get("isolation_backend", "docker")
    )
    _write_record(stage.parent / _CREATED, {**intent, "container_id": container})
    runtime._call(["start", "--attach", container], timeout=profile.timeout_sec + 5)
    final = inspect()["State"]
    if final["ExitCode"] != 0 or final["Running"]:
        raise NativeRuntimeError("Mounted kernel process failed")
