"""Local single-user OCI execution; never a fallback to unsandboxed custom code.

Custom code has no writable host mount. Outputs and scratch live on bounded
tmpfs mounts and are copied out while the container is alive, then validated.
Shared-service hostile workloads require a stronger runtime boundary.
"""
from __future__ import annotations

import io
import os
from pathlib import Path, PurePosixPath
import selectors
import shutil
import subprocess
import tarfile
import time

from devharness_core.provenance import canonical_json, copy_artifact


def _path(value):
    path = Path(value)
    if not path.is_absolute() or path != path.resolve() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Canonical absolute paths without symlinks are required")
    if any(c in str(path) for c in (",", "\n", "\r")):
        raise ValueError("Path cannot be represented safely as a Docker mount")
    return path


def container_command(contract, workspace, name, image_volumes=()):
    """Build argv only; no interpolation of user code into a shell command."""
    workspace = _path(workspace)
    limits = contract["execution"]
    uid, gid = os.getuid() or 65532, os.getgid() if os.getuid() else 65532
    command = ["create", "--name=" + name, "--pull=never", "--read-only", "--network=none",
               "--cap-drop=ALL", "--security-opt=no-new-privileges:true", "--no-healthcheck",
               "--user=" + str(uid) + ":" + str(gid), "--pids-limit=" + str(limits["pids"]),
               "--cpus=" + str(limits["cpu"]), "--memory=" + str(limits["memory_mb"]) + "m",
               "--memory-swap=" + str(limits["memory_mb"]) + "m", "--log-driver=none",
               "--ulimit=nofile=256:256", "--ulimit=core=0:0", "--workdir=/tmp",
               "--entrypoint=/bin/sh"]
    for directory in ("inputs", "method"):
        command += ["--mount", f"type=bind,src={workspace / directory},dst=/{directory},readonly"]
    mounts = {"/tmp": min(limits["memory_mb"] * 1024**2 // 4, 64 * 1024**2),
              "/outputs": limits["max_output_bytes"]}
    for target in image_volumes:
        path = PurePosixPath(target)
        if (not path.is_absolute() or str(path) != target or ".." in path.parts
                or target in {"/", "/inputs", "/method", "/proc", "/sys", "/dev"}
                or any(target.startswith(prefix + "/") for prefix in ("/inputs", "/method", "/proc", "/sys", "/dev"))):
            raise ValueError("Unsupported image volume")
        mounts.setdefault(target, 1024**2)
    for target, size in mounts.items():
        command.append(f"--tmpfs={target}:rw,nosuid,nodev,noexec,size={size},uid={uid},gid={gid},mode=0700")
    # This fixed supervisor keeps tmpfs alive until outputs have been copied.
    command += [contract["environment"]["image"], "-c", "while :; do sleep 1; done"]
    return command


def _bounded(command, env, timeout, maximum):
    """Drain both pipes with one cumulative byte bound and a monotonic deadline."""
    started = time.monotonic()
    process = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    buffers, total = {"stdout": bytearray(), "stderr": bytearray()}, 0
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                if time.monotonic() - started > timeout:
                    raise TimeoutError("Method exceeded its wall-time limit")
                for key, _ in selector.select(min(.1, timeout)):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    available = max(0, maximum - total)
                    buffers[key.data].extend(chunk[:available])
                    total += len(chunk)
                    if total > maximum:
                        raise ValueError("Method output or logs exceeded the byte limit")
        process.wait(timeout=max(.01, timeout - (time.monotonic() - started)))
        return process.returncode, bytes(buffers["stdout"]), bytes(buffers["stderr"])
    except (TimeoutError, ValueError, subprocess.TimeoutExpired) as error:
        error.execution = {"exit_code": None, "runtime": "local_docker",
                           "stdout": bytes(buffers["stdout"][:500000]).decode("utf-8", "replace"),
                           "stderr": bytes(buffers["stderr"][:500000]).decode("utf-8", "replace"),
                           "error": str(error)}
        raise
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
        process.stderr.close()


def _extract(data, output_dir, maximum):
    total, count, names = 0, 0, set()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        for member in archive:
            count += 1
            if count > 64:
                raise ValueError("Too many output archive members")
            name = member.name.removeprefix("./")
            if member.isdir() and name in {"", "."}:
                continue
            path = PurePosixPath(name)
            if not member.isfile() or len(path.parts) != 1 or name in {"", ".", ".."} or name in names:
                raise ValueError("Only distinct regular output files are supported")
            names.add(name)
            total += member.size
            if total > maximum:
                raise ValueError("Method outputs exceeded the byte limit")
            with archive.extractfile(member) as source, (output_dir / name).open("xb") as target:
                shutil.copyfileobj(source, target, 1024**2)


class LocalDockerExecutor:
    """Use an installed digest-pinned image and one explicitly local daemon."""

    def execute(self, contract, inputs, parameters, source, lock, output_dir):
        from devharness_controlled.native_runtime import LocalDocker
        output_dir = _path(output_dir)
        workspace = output_dir.parent
        runtime = LocalDocker()
        runtime.isolate_client(workspace / "docker-client")
        runtime.env = {"PATH": os.defpath, "HOME": str(workspace / "docker-client")}
        identity = runtime._call(["image", "inspect", contract["environment"]["image"], "--format",
            '{"id":{{json .Id}},"digests":{{json .RepoDigests}},"volumes":{{json .Config.Volumes}},"os":{{json .Os}}}'],
            json_result=True)
        if (contract["environment"]["image"] not in (identity["digests"] or []) or identity["os"] != "linux"
                or len(identity["volumes"] or {}) > 16):
            raise ValueError("Installed image identity is unsupported or mismatched")
        (workspace / "inputs").mkdir(mode=0o755)
        (workspace / "method").mkdir(mode=0o755)
        for name, path in inputs.items():
            copy_artifact(path, workspace / "inputs" / name, max_bytes=100_000_000, max_entries=1)
        copy_artifact(source, workspace / "method" / "main.py", max_bytes=1_000_000, max_entries=1)
        copy_artifact(lock, workspace / "method" / "environment.lock", max_bytes=1_000_000, max_entries=1)
        (workspace / "method" / "parameters.json").write_bytes(canonical_json(parameters))
        for directory in (workspace / "inputs", workspace / "method"):
            directory.chmod(0o755)
            for path in directory.iterdir():
                path.chmod(0o444)
        name = "devharness-method-" + workspace.name
        created = False
        try:
            runtime._call(container_command(contract, workspace, name, identity["volumes"] or {}))
            created = True
            runtime._call(["start", name])
            command = runtime.command(["exec", name, "/usr/bin/env", "-i", "PATH=/usr/local/bin:/usr/bin:/bin",
                "HOME=/tmp", "TMPDIR=/tmp", "PYTHONDONTWRITEBYTECODE=1", "OMP_NUM_THREADS=1",
                *contract["implementation"]["entrypoint"]])
            code, stdout, stderr = _bounded(command, runtime.env, contract["execution"]["timeout_seconds"], 1_000_000)
            result = {"exit_code": code, "runtime": "local_docker", "image_id": identity["id"],
                      "image": contract["environment"]["image"], "stdout": stdout.decode("utf-8", "replace"),
                      "stderr": stderr.decode("utf-8", "replace"), "container_name": name}
            if code == 0:
                maximum = contract["execution"]["max_output_bytes"]
                # Docker cp omits tmpfs content. Stream a bounded archive from
                # inside the container; never use tar's filesystem extractor.
                code, archive, error = _bounded(runtime.command(["exec", name, "/usr/bin/env", "-i",
                    "PATH=/usr/local/bin:/usr/bin:/bin", "tar", "-C", "/outputs", "-cf", "-", "."]),
                    runtime.env, 30, maximum + 1024**2)
                if code:
                    raise ValueError("Unable to collect sandbox outputs: " + error.decode("utf-8", "replace")[:1000])
                _extract(archive, output_dir, maximum)
            return result
        finally:
            if created:
                runtime._call(["rm", "--force", "--volumes", name])
