"""Local OCI transport for the fixed, digest-pinned mounted RNA worker."""

import fcntl
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

from devharness_controlled.native_contracts import limma_voom_kernel_images
from devharness_controlled.native_runtime import LocalDocker, NativeRuntimeError

_LABEL = "io.devbench.mounted-request-sha256"
_INTENT = "oci-create-intent.json"
_CREATED = "oci-created.json"
_RECOVERED = "oci-cleanup.json"


def require_mounted_pipeline_runtime(
    spec, image_id, *, representation, allow_python_wald=False,
):
    """Apply the installed image policy at the application execution boundary."""
    spec.require_mounted_runtime(
        image_id, representation=representation, allow_python_wald=allow_python_wald,
        approved_voom_image_ids=limma_voom_kernel_images(),
    )


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
    identity = runtime._call(["info", "--format", "{{.ID}}"])
    if not re.fullmatch(r"[A-Za-z0-9:._-]{1,256}", identity):
        raise NativeRuntimeError("Mounted Docker daemon identity unavailable")
    return {"endpoint": runtime.endpoint, "daemon_id": identity}


@contextmanager
def _private_runtime(directory, **options):
    runtime = LocalDocker(**options)
    with tempfile.TemporaryDirectory(dir=directory, prefix=".oci-client-") as client:
        runtime.isolate_client(Path(client) / "config")
        yield runtime


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


def _validate_cleanup_record(directory, binding, proof):
    intent = _optional_record(directory, _INTENT)
    created = _optional_record(directory, _CREATED)
    disposition = proof.get("disposition")
    if disposition == "not_created":
        valid = (
            intent is None
            and created is None
            and proof
            == {**binding, "disposition": disposition, "cleanup_confirmed": True}
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


def recover_mounted_container(stage, *, request_sha, image_id, attempt_id):
    """Fence an orphaned worker and verify removal on the launch daemon."""
    from .tier_a_executor import _write_record

    binding = _identity(request_sha, image_id, attempt_id)
    directory = stage.parent
    with _oci_lock(directory):
        proof = _optional_record(directory, _RECOVERED)
        if proof is not None:
            return _validate_cleanup_record(directory, binding, proof)
        intent = _optional_record(directory, _INTENT)
        if intent is None:
            if _optional_record(directory, _CREATED) is not None:
                raise NativeRuntimeError("Missing mounted OCI create intent")
            proof = {**binding, "disposition": "not_created", "cleanup_confirmed": True}
        else:
            if any(
                intent.get(key) != value for key, value in binding.items()
            ) or not isinstance(intent.get("endpoint"), str):
                raise NativeRuntimeError("Mounted OCI launch record mismatch")
            with _private_runtime(stage, endpoint=intent["endpoint"]) as runtime:
                if intent != {**binding, **_daemon(runtime)}:
                    raise NativeRuntimeError("Mounted OCI recovery daemon mismatch")
                proof = _clean(runtime, stage, intent)
                if intent != {**binding, **_daemon(runtime)}:
                    raise NativeRuntimeError("Mounted OCI recovery daemon mismatch")
        _write_record(directory / _RECOVERED, proof)
        return proof


def _configuration(runtime, image_id):
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
    labels = facts["labels"] or {}
    if (
        facts["id"] != image_id
        or facts["os"] != "linux"
        or facts["arch"] not in {"arm64", "amd64"}
        or labels.get("org.opencontainers.image.title") != "devbench-rna-kernel"
        or labels.get("org.opencontainers.image.version") != "2.0"
    ):
        raise NativeRuntimeError("Mounted kernel image or platform mismatch")
    return "linux/" + facts["arch"]


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
):
    if os.geteuid() == 0:
        uid, gid = 65532, 65532
        (stage / "inputs").chmod(0o555)
        for path in (stage / "inputs").rglob("*"):
            path.chmod(0o555 if path.is_dir() else 0o444)
        os.chown(stage / "outputs", uid, gid)
    else:
        uid, gid = os.geteuid(), os.getegid()
    return [
        "create",
        "--name",
        name,
        "--pull=never",
        "--platform",
        platform,
        "--network",
        "none",
        "--read-only",
        "--security-opt",
        "no-new-privileges=true",
        "--cap-drop",
        "ALL",
        "--user",
        f"{uid}:{gid}",
        "--pids-limit",
        "64",
        "--memory",
        f"{memory_mb}m",
        "--memory-swap",
        f"{memory_mb}m",
        "--cpus",
        "2",
        "--log-driver",
        "none",
        "--ipc",
        "private",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=67108864,mode=1777",
        "--label",
        f"{_LABEL}={request_sha}",
        "--mount",
        f"type=bind,src={stage / 'inputs'},dst=/inputs,readonly",
        "--mount",
        f"type=bind,src={stage / 'outputs'},dst=/outputs",
        "--entrypoint",
        "/usr/bin/timeout",
        image,
        "--signal=KILL",
        str(timeout),
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


def run_mounted_container(
    stage,
    *,
    request_sha,
    image_id,
    attempt_id,
    timeout,
    memory_mb,
    worker_module="rna_kernels.mounted_bulk",
):
    """Run only private sibling mounts and return a cleanup-verified outcome."""
    from .tier_a_executor import _write_record

    if worker_module not in (
        "rna_kernels.mounted_bulk",
        "rna_kernels.mounted_scrna",
        "devharness_server.tools_scrna_composition",
        "devharness_server.tools_rna_pathways",
    ):
        raise ValueError("Mounted Tier A requires a registered worker module")
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
        with _private_runtime(stage) as runtime:
            platform = _configuration(runtime, image_id)
            arguments = _mount_arguments(
                stage,
                image_id,
                binding["name"],
                request_sha,
                timeout,
                memory_mb,
                platform,
                worker_module=worker_module,
            )
            intent = {**binding, **_daemon(runtime)}
            _write_record(directory / _INTENT, intent)
            error = None
            try:
                container = runtime._call(arguments)
                if not re.fullmatch(r"[a-f0-9]{64}", container):
                    raise NativeRuntimeError(
                        "Docker returned an invalid mounted kernel container ID"
                    )
                _start_container(runtime, stage, intent, container, timeout)
            except NativeRuntimeError as rejected:
                error = type(rejected).__name__
            proof = _clean(runtime, stage, intent)
            if intent != {**binding, **_daemon(runtime)}:
                raise NativeRuntimeError("Mounted OCI cleanup daemon mismatch")
            _write_record(directory / _RECOVERED, proof)
        return {**proof, "platform": platform, "error_type": error}


def _start_container(runtime, stage, intent, container, timeout):
    from .tier_a_executor import _write_record

    def inspect():
        return _owned(
            runtime,
            intent["name"],
            intent["image_id"],
            intent["request_sha256"],
            container,
        )

    _verify_mounts(inspect(), stage)
    _write_record(stage.parent / _CREATED, {**intent, "container_id": container})
    runtime._call(["start", "--attach", container], timeout=timeout + 5)
    final = inspect()["State"]
    if final["ExitCode"] != 0 or final["Running"]:
        raise NativeRuntimeError("Mounted kernel process failed")
