"""Stage a frozen script on the daemon's existing cleanup-verifiable OCI transport."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import time
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

from devbench.engine import mounted_kernel_oci as oci
from devbench.engine.tier_a_executor import _write_record
from devbench.engine.workflow_operations import OperationBlocked
from devbench.platform.plugin_jobs import canonical, digest

from .approval import verified_isolation
from .registry import tier2_base_images

_PORT = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,95}\Z")


def _stage(path):
    if not path.is_absolute() or any(c.is_symlink() for c in (path, *path.parents)):
        raise OperationBlocked("input_scope_violation")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Anchor creation and ownership checks to no-follow descriptors. A symlink
    # swap below a writable parent cannot redirect chmod/chown onto user files.
    flags = os.O_NOFOLLOW | os.O_DIRECTORY | os.O_RDONLY
    with ExitStack() as opened:
        parent = os.open("/", flags)
        opened.callback(os.close, parent)
        for component in path.parts[1:]:
            parent = os.open(component, flags, dir_fd=parent)
            opened.callback(os.close, parent)
        for member in ("inputs", "outputs"):
            try:
                os.mkdir(member, mode=0o700, dir_fd=parent)
            except FileExistsError:
                pass
            fd = os.open(member, flags, dir_fd=parent)
            opened.callback(os.close, fd)
            info = os.fstat(fd)
            if info.st_mode & 0o022 or info.st_uid != os.getuid():
                raise OperationBlocked("input_scope_violation")
            if member == "outputs" and os.geteuid() == 0:
                from devbench.engine.method_executor import _container_ids

                os.fchown(fd, *_container_ids())
    return path


def _image(runtime, image):
    observed = runtime._call(
        [
            "image",
            "inspect",
            image,
            "--format",
            '{"id":{{json .Id}},"os":{{json .Os}},"arch":{{json .Architecture}},'
            '"volumes":{{json (index .Config "Volumes")}}}',
        ],
        json_result=True,
        timeout=2,
    )
    if observed["id"] != image or observed["os"] != "linux" or observed["volumes"]:
        raise OperationBlocked("runtime_prerequisite_missing")
    return "linux/" + observed["arch"]


def _command(stage, identity, limits, platform, language, curated=False):
    from devbench.engine.method_executor import _container_ids

    uid, gid = _container_ids()
    timeout = limits.get("timeout_sec", 300)
    memory = limits.get("memory_mb", 512)
    cpus = limits.get("cpus", 1)
    # These are the same two stage mounts and ownership label checked during
    # orphan reconciliation by recover_mounted_container().
    command = [
        "create",
        "--name",
        identity["name"],
        "--pull=never",
        "--platform",
        platform,
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges=true",
        "--user",
        f"{uid}:{gid}",
        "--pids-limit",
        "64",
        "--memory",
        f"{memory}m",
        "--memory-swap",
        f"{memory}m",
        "--cpus",
        str(cpus),
        "--ulimit",
        "fsize=67108864:67108864",
        "--log-driver",
        "none",
        "--ipc",
        "private",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=67108864,mode=1777",
        "--label",
        f"{oci._LABEL}={identity['request_sha256']}",
        "--mount",
        f"type=bind,src={stage / 'inputs'},dst=/inputs,readonly,"
        "bind-recursive=disabled",
        "--mount",
        f"type=bind,src={stage / 'outputs'},dst=/outputs,bind-recursive=disabled",
        "--workdir",
        "/outputs",
        "--entrypoint",
        "/usr/bin/timeout",
        identity["image_id"],
        "--signal=KILL",
        str(timeout),
    ]
    interpreter = (
        ["python3", "-I", "-B", "/inputs/script.py"]
        if language == "python"
        else [
            "Rscript",
            "--vanilla",
            "-e",
            "invisible(parse('/inputs/script.R')); "
            "source('/inputs/script.R', chdir=FALSE)",
        ]
    )
    if curated:
        interpreter = ["python3.13", "-P", "-s", "-B", "/inputs/script.py"]
    return [
        *command,
        "python3",
        "-I",
        "-S",
        "-B",
        "/inputs/.devbench-launch.py",
        *interpreter,
    ]


def _output_budget(outputs):
    total = 0
    with os.scandir(outputs) as entries:
        for count, entry in enumerate(entries, start=1):
            info = entry.stat(follow_symlinks=False)
            total += info.st_size
            if (
                count > 64
                or total > 256 * 1024**2
                or info.st_size > 64 * 1024**2
                or not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
            ):
                raise OperationBlocked("resource_quota_exceeded")


def _start_bounded(jobs, rid, runtime, container, stage, timeout):
    end = time.monotonic() + timeout + 2
    with jobs.worker_admission(rid):
        runtime._call(["start", container], timeout=3)
    while True:
        _output_budget(stage / "outputs")
        observed = runtime._call(
            ["container", "inspect", container, "--format", "{{json .State}}"],
            json_result=True,
            timeout=2,
        )
        if not observed["Running"]:
            _output_budget(stage / "outputs")
            if observed["ExitCode"] != 0:
                raise OperationBlocked("scientific_refusal")
            return
        if time.monotonic() >= end:
            raise OperationBlocked("resource_quota_exceeded")
        time.sleep(0.05)


def isolated(jobs, plan, stage, *, rid=None):
    image = plan["exploratory_code"]["base_image_id"]
    lang = plan["exploratory_code"]["language"]
    if not plan.get("_curated_diagnostic") and not any(
        row["image_id"] == image and row["language"] == lang
        for row in tier2_base_images()
    ):
        raise OperationBlocked("runtime_prerequisite_missing")
    sha, attempt = digest(plan), uuid4().hex
    identity = oci._identity(sha, image, attempt)
    if rid:
        jobs.stage(
            rid,
            mounted_container={
                "stage": str(stage),
                "request_sha": sha,
                "image_id": image,
                "attempt_id": attempt,
            },
        )
    limits = plan.get("resource_override") or {}
    with oci._oci_lock(stage.parent), oci._private_runtime(stage) as runtime:
        command = _command(
            stage,
            identity,
            limits,
            _image(runtime, image),
            lang,
            plan.get("_curated_diagnostic", False),
        )
        intent = {**identity, **oci._daemon(runtime)}
        _write_record(stage.parent / oci._INTENT, intent)
        try:
            container = runtime._call(command, timeout=3 if rid is None else 30)
            observed = oci._owned(runtime, intent["name"], image, sha, container)
            _write_record(
                stage.parent / oci._CREATED, {**intent, "container_id": container}
            )
            proof = {"stage": str(stage), "document": observed}
            if not verified_isolation(jobs.workspace, plan, proof):
                raise OperationBlocked("runtime_prerequisite_missing")
            if rid:
                _start_bounded(
                    jobs, rid, runtime, container, stage, limits.get("timeout_sec", 300)
                )
            return proof
        finally:
            cleanup = oci._clean(runtime, stage, intent)
            if oci._daemon(runtime) != {
                k: intent[k] for k in ("daemon_id", "endpoint")
            }:
                raise OperationBlocked("runtime_prerequisite_missing")
            _write_record(stage.parent / oci._RECOVERED, cleanup)


def inspect_isolation(jobs, plan):
    # A no-start probe observes actual daemon configuration before auto-approval.
    # The worker independently checks its data-bearing container before start.
    stage = _stage(
        jobs.workspace / ".devbench-daemon" / "policy-probes" / uuid4().hex / "stage"
    )
    try:
        return isolated(jobs, plan, stage)
    except (ValueError, RuntimeError, OSError):
        return None


def _stage_inputs(ctx, bindings, directory):
    from .intake import verify_artifact_file, verify_artifact_identity

    manifest = {}
    for port, record in bindings.items():
        if not _PORT.fullmatch(port):
            raise OperationBlocked("invalid_request")
        ref = record["ref"]
        source = Path(record["path"])
        if port in {"bindings.json", "parameters.json", "script.py", "script.R"}:
            raise OperationBlocked("invalid_request")
        dest = directory / port
        if source.is_dir():
            verify_artifact_identity(
                ctx, record, until=float("inf"), allow_private=ctx.offline_only
            )
            # Reject symlinks and special files during the mounted snapshot.
            from ._sources import DirectoryInventory

            shutil.copytree(source, dest, symlinks=True)
            if DirectoryInventory(dest, float("inf")).sha256 != (
                record["detail"].get("source_sha256") or ref["sha256"]
            ):
                raise OperationBlocked("plan_identity_mismatch")
        else:
            with (
                verify_artifact_file(
                    ctx, record, until=float("inf"), allow_private=ctx.offline_only
                ) as stream,
                dest.open("xb") as out,
            ):
                sha = hashlib.sha256()
                while block := stream.read(1024 * 1024):
                    out.write(block)
                    sha.update(block)
            native_sha = record["detail"].get("source_sha256") or ref["sha256"]
            if sha.hexdigest() != native_sha:
                raise OperationBlocked("plan_identity_mismatch")
        manifest[port] = {
            "path": "/inputs/" + dest.name,
            "format": ref["format"],
            "representation_class": ref["representation_class"],
        }
    return manifest


def run_script(ctx, jobs, rid, plan, bindings, directory):
    code = plan["exploratory_code"]
    source = code["source_code"].encode()
    if hashlib.sha256(source).hexdigest() != plan["script_sha256"]:
        raise OperationBlocked("plan_identity_mismatch")
    stage = _stage(directory / "stage")
    inputs = stage / "inputs"
    script = inputs / ("script.py" if code["language"] == "python" else "script.R")
    script.write_bytes(source)
    shutil.copyfile(
        Path(__file__).with_name("guest_launch.py"), inputs / ".devbench-launch.py"
    )
    manifest = _stage_inputs(ctx, bindings, inputs)
    (inputs / "bindings.json").write_bytes(canonical(manifest))
    (inputs / "parameters.json").write_bytes(canonical(code.get("parameters") or {}))
    if os.geteuid() == 0:
        from devbench.engine.method_executor import _container_ids

        uid, gid = _container_ids()
        os.chown(stage / "outputs", uid, gid)
        inputs.chmod(0o755)
        for path in inputs.rglob("*"):
            path.chmod(0o755 if path.is_dir() else 0o444)
    try:
        isolated(jobs, plan, stage, rid=rid)
        # The runtime is gone. Detach files from its writable mount before
        # publishing them, including outputs that will subsequently be rejected.
        retained = directory / "produced"
        (stage / "outputs").rename(retained)
        (stage / "outputs").mkdir(mode=0o700)
        return retained
    finally:
        if (stage.parent / oci._RECOVERED).is_file():
            shutil.rmtree(inputs)
            shutil.rmtree(stage / "outputs")
            inputs.mkdir(mode=0o700)
            (stage / "outputs").mkdir(mode=0o700)
