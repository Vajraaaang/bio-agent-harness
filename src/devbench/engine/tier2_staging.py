"""Disk-backed, per-attempt output staging on the local OCI daemon's filesystem."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

LABEL = "devbench.exploratory.staging"
ENTRY_LIMIT = 100_000
# Runs from the pinned read-only image, as container PID 1. Losing the client
# leaves both the aggregate staging monitor and the wall deadline in force.
SUPERVISOR = """
import os, signal, stat, sys, time
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
maximum, lifetime, entries = map(float, sys.argv[1:])
deadline = time.monotonic() + lifetime
while time.monotonic() < deadline:
    total = count = 0
    todo = ['/outputs']
    while todo:
        with os.scandir(todo.pop()) as directory:
            for row in directory:
                try:
                    info = row.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                count += 1
                if stat.S_ISDIR(info.st_mode):
                    todo.append(row.path)
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    total += info.st_size
                else:
                    sys.exit(74)
                if total > maximum or count > entries:
                    sys.exit(75)
    time.sleep(.1)
sys.exit(124)
"""

# Check what the worker kernel actually mounted, including per-mount security
# flags. Docker's local driver accepts 'bind,noexec' but Linux ignores noexec
# on an initial bind. An overlay filesystem mount applies the flags directly.
MOUNT_CHECK = """
import os, stat
path = '/outputs'
entries = [s.split() for s in open('/proc/self/mountinfo') if s.split()[4] == path]
assert len(entries) == 1
entry = entries[0]
assert {'rw','nosuid','nodev','noexec'} <= set(entry[5].split(','))
dash = entry.index('-')
assert entry[dash+1] == 'overlay' and 'rw' in entry[dash+3].split(',')
mode = os.stat(path)
assert mode.st_uid == os.getuid() and stat.S_IMODE(mode.st_mode) == 0o700
print('verified')
"""


def output_volume(name):
    return "devbench-stage-" + hashlib.sha256(name.encode()).hexdigest()[:32]


def supervisor_argv(limits):
    return [
        "-I",
        "-S",
        "-c",
        SUPERVISOR,
        str(limits["max_output_bytes"]),
        str(limits["timeout_seconds"] + 8),
        str(ENTRY_LIMIT),
    ]


def _volume(runtime, name, binding, *, options=None):
    label = binding["inventory_sha256"] + "." + binding["nextflow_session_id"]
    command = ["volume", "create", "--driver", "local", "--label", f"{LABEL}={label}"]
    for key, value in (options or {}).items():
        command.extend(["--opt", key + "=" + value])
    runtime._call([*command, name])
    volume = runtime._call(
        ["volume", "inspect", name, "--format", "{{json .}}"], json_result=True
    )
    if (
        volume["Name"] != name
        or volume["Driver"] != "local"
        or volume.get("Labels", {}).get(LABEL) != label
        or (volume.get("Options") or {}) != (options or {})
    ):
        raise PermissionError("Staging volume ownership or mount options differ")
    return volume


def prepare_storage(runtime, binding, image, uid, gid):
    name = output_volume(binding["container_name"])
    backing = _volume(runtime, name + "-disk", binding)
    path = backing["Mountpoint"]
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", path) or ".." in Path(path).parts:
        raise PermissionError("Unexpected local staging mountpoint")
    # Docker cp with archive ownership works with native Linux and with a
    # Docker Desktop VM. This container is only a filesystem handle, never run.
    seed = name + "-seed"
    label = binding["inventory_sha256"] + "." + binding["nextflow_session_id"]
    container = runtime._call(
        [
            "create",
            "--name",
            seed,
            "--pull=never",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--ipc=private",
            f"--user={uid}:{gid}",
            "--label",
            f"{LABEL}={label}",
            "--mount",
            f"type=volume,src={name}-disk,dst=/staging,volume-nocopy",
            "--entrypoint=/bin/true",
            image,
        ]
    )
    if not re.fullmatch(r"[a-f0-9]{64}", container):
        raise PermissionError("Invalid staging initializer identity")
    try:
        with tempfile.TemporaryDirectory(dir=Path(binding["output_dir"]).parent) as raw:
            for leaf in ("empty", "outputs", "work"):
                directory = Path(raw) / leaf
                directory.mkdir(mode=0o700)
                if os.getuid() == 0:
                    os.chown(directory, uid, gid)
            runtime._call(["cp", "-a", raw + "/.", container + ":/staging"])
    finally:
        runtime._call(["rm", "--volumes", container])
    opts = {
        "type": "overlay",
        "device": "overlay",
        "o": f"rw,nosuid,nodev,noexec,lowerdir={path}/empty,"
        f"upperdir={path}/outputs,workdir={path}/work",
    }
    _volume(runtime, name, binding, options=opts)


def remove_storage(runtime, binding):
    """Remove only inventory-labeled staging objects for this exact attempt."""
    name = output_volume(binding["container_name"])
    label = binding["inventory_sha256"] + "." + binding["nextflow_session_id"]
    selector = f"label={LABEL}={label}"
    seed = runtime._call(
        [
            "container",
            "ls",
            "-aq",
            "--no-trunc",
            "--filter",
            selector,
            "--filter",
            "name=^/" + re.escape(name + "-seed") + "$",
        ]
    )
    if seed:
        if not re.fullmatch(r"[a-f0-9]{64}", seed):
            raise PermissionError("Ambiguous staging initializer")
        runtime._call(["rm", "--volumes", seed])
    volumes = runtime._call(["volume", "ls", "-q", "--filter", selector]).splitlines()
    if set(volumes) - {name, name + "-disk"}:
        raise PermissionError("Unexpected staging volume identity")
    for target in (name, name + "-disk"):
        if target in volumes:
            runtime._call(["volume", "rm", target])
    if runtime._call(["volume", "ls", "-q", "--filter", selector]):
        raise PermissionError("Staging cleanup is unconfirmed")


def verify_live_mount(runtime, container):
    reply = runtime._call(["exec", container, "python3", "-I", "-S", "-c", MOUNT_CHECK])
    if reply != "verified":
        raise PermissionError("Disk output mount security flags are unverified")
