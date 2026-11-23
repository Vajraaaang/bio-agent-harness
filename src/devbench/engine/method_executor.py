"""Local single-user OCI execution; never a fallback to unsandboxed custom code.

Custom code has no writable host mount. Outputs and scratch live on bounded
tmpfs mounts and are copied out while the container is alive, then validated.
Shared-service hostile workloads require a stronger runtime boundary.
"""

from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
import re
import selectors
import shutil
import subprocess
import tarfile
import tempfile
import time
import uuid
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from pathlib import Path, PurePosixPath

from .provenance import canonical_json, copy_artifact, hash_artifact


def _path(value):
    path = Path(value)
    if (
        not path.is_absolute()
        or path != path.resolve()
        or any(p.is_symlink() for p in (path, *path.parents))
    ):
        raise ValueError("Canonical absolute paths without symlinks are required")
    if any(c in str(path) for c in (",", "\n", "\r")):
        raise ValueError("Path cannot be represented safely as a Docker mount")
    return path


EXPLORATORY_IMAGE = (
    "python@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159"
)
EXPLORATORY_IMAGE_ID = (
    "sha256:ff710677e697054c516f12c53c92166f1aef075f21b2a593f4c615e522112b4e"
)
_LIFECYCLE_KEYS = (
    "run_id",
    "container_name",
    "image",
    "inventory_sha256",
    "nextflow_session_id",
    "profile",
)
_COLLECTION_KEYS = ("container_id", "nextflow_session_id", "image")
_BASE_ULIMITS = {"nofile": 256, "core": 0}


def _container_ids():
    uid = os.getuid()
    return (uid, os.getgid()) if uid else (65532, 65532)


def _tmpfs_sizes(limits):
    return {
        "/tmp": min(limits["memory_mb"] * 1024**2 // 4, 64 * 1024**2),
        "/outputs": limits["max_output_bytes"],
    }


def _tmpfs_options(size, uid, gid):
    return f"rw,nosuid,nodev,noexec,size={size},uid={uid},gid={gid},mode=0700"


def container_command(
    contract, workspace, name, image_volumes=(), *, labels=None, exploratory=False
):
    """Build argv only; no interpolation of user code into a shell command."""
    workspace = _path(workspace)
    limits = contract["execution"]
    uid, gid = _container_ids()
    command = [
        "create",
        "--name=" + name,
        "--pull=never",
        "--read-only",
        "--network=none",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--no-healthcheck",
        "--user=" + str(uid) + ":" + str(gid),
        "--pids-limit=" + str(limits["pids"]),
        "--cpus=" + str(limits["cpu"]),
        "--memory=" + str(limits["memory_mb"]) + "m",
        "--memory-swap=" + str(limits["memory_mb"]) + "m",
        "--log-driver=none",
        *(f"--ulimit={name}={size}:{size}" for name, size in _BASE_ULIMITS.items()),
        "--workdir=/tmp",
        "--entrypoint=/bin/sh",
    ]
    for directory in ("inputs", "method"):
        mount = f"type=bind,src={workspace / directory},dst=/{directory},readonly"
        command += [
            "--mount",
            mount + (",bind-recursive=disabled" if exploratory else ""),
        ]
    if exploratory:
        maximum = limits["max_output_bytes"]
        command += [
            "--ipc=private",
            "--restart=no",
            f"--ulimit=fsize={maximum}:{maximum}",
        ]
    mounts = _tmpfs_sizes(limits)
    for target in image_volumes:
        path = PurePosixPath(target)
        if (
            not path.is_absolute()
            or str(path) != target
            or ".." in path.parts
            or target in {"/", "/inputs", "/method", "/proc", "/sys", "/dev"}
            or any(
                target.startswith(prefix + "/")
                for prefix in ("/inputs", "/method", "/proc", "/sys", "/dev")
            )
        ):
            raise ValueError("Unsupported image volume")
        mounts.setdefault(target, 1024**2)
    for target, size in mounts.items():
        command.append(f"--tmpfs={target}:{_tmpfs_options(size, uid, gid)}")
    if labels:
        command += [
            "--label=" + key + "=" + value for key, value in sorted(labels.items())
        ]
    if contract.get("method_id") == "community.nfcore.differentialabundance.2":
        from devbench.analysis.community_workflow import _resources

        profile = _resources(
            contract["parameters"]["properties"]["plan"]["properties"]["resources"][
                "const"
            ]
        )
        if any(
            profile[k] != limits[k]
            for k in ("cpu", "memory_mb", "timeout_seconds", "pids", "max_output_bytes")
        ):
            raise ValueError(
                "Community resource profile differs from immutable method "
                "execution limits"
            )
        # PID 1 exits independently of the host coordinator. Namespace teardown
        # stops all descendants, including an orphaned local Nextflow engine.
        command = [item for item in command if item != "--entrypoint=/bin/sh"]
        command += [
            "--entrypoint=python3",
            "--tmpfs=/tmp/community:"
            + _tmpfs_options(profile["scratch_bytes"], uid, gid),
            contract["environment"]["image"],
            "-c",
            "import time; time.sleep(" + str(limits["timeout_seconds"]) + ")",
        ]
    elif exploratory:
        # A small bounded grace period allows trusted collection after exec.
        # This PID-namespace lifetime survives the loss of the coordinator.
        command.remove("--entrypoint=/bin/sh")
        command += [
            "--entrypoint=/bin/sleep",
            contract["environment"]["image"],
            str(limits["timeout_seconds"] + 8),
        ]
    else:
        # Historical generic-method lifetime remains host-supervised.
        command += [contract["environment"]["image"], "-c", "while :; do sleep 1; done"]
    return command


def _bounded(command, env, timeout, maximum, *, stdout_sink=None):
    """Drain both pipes with one cumulative byte bound and a monotonic deadline."""
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    buffers, total = {"stdout": bytearray(), "stderr": bytearray()}, 0
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                if time.monotonic() - started > timeout:
                    raise TimeoutError("Method exceeded its wall-time limit")
                for key, _ in selector.select(min(0.1, timeout)):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    available = max(0, maximum - total)
                    if key.data == "stdout" and stdout_sink is not None:
                        stdout_sink.write(chunk[:available])
                    elif stdout_sink is not None:
                        buffers[key.data].extend(
                            chunk[
                                : min(
                                    available, max(0, 500_000 - len(buffers[key.data]))
                                )
                            ]
                        )
                    else:
                        buffers[key.data].extend(chunk[:available])
                    total += len(chunk)
                    if total > maximum:
                        raise ValueError(
                            "Method output or logs exceeded the byte limit"
                        )
        process.wait(timeout=max(0.01, timeout - (time.monotonic() - started)))
        return process.returncode, bytes(buffers["stdout"]), bytes(buffers["stderr"])
    except (TimeoutError, ValueError, subprocess.TimeoutExpired) as error:
        error.execution = {
            "exit_code": None,
            "runtime": "local_docker",
            "stdout": bytes(buffers["stdout"][:500000]).decode("utf-8", "replace"),
            "stderr": bytes(buffers["stderr"][:500000]).decode("utf-8", "replace"),
            "error": str(error),
        }
        raise
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
        process.stderr.close()


def _extract(data, output_dir, maximum):
    total, count, names = 0, 0, set()
    with tarfile.open(
        fileobj=io.BytesIO(data) if isinstance(data, bytes) else data, mode="r:"
    ) as archive:
        for member in archive:
            count += 1
            if count > 64:
                raise ValueError("Too many output archive members")
            name = member.name.removeprefix("./")
            if member.isdir() and name in {"", "."}:
                continue
            path = PurePosixPath(name)
            if (
                not member.isfile()
                or len(path.parts) != 1
                or name in {"", ".", ".."}
                or name in names
            ):
                raise ValueError("Only distinct regular output files are supported")
            names.add(name)
            total += member.size
            if total > maximum:
                raise ValueError("Method outputs exceeded the byte limit")
            with (
                archive.extractfile(member) as source,
                (output_dir / name).open("xb") as target,
            ):
                shutil.copyfileobj(source, target, 1024**2)


class LocalDockerExecutor:
    """Use an installed digest-pinned image and one explicitly local daemon."""

    def __init__(self):
        self._observer_context = ContextVar(
            "devbench_oci_lifecycle_observer", default=None
        )

    def set_observer(self, observer):
        if not callable(observer):
            raise TypeError("Use a trusted lifecycle observer")
        self._observer_context.set(observer)

    @staticmethod
    def _runtime(workspace):
        from devharness_controlled.native_runtime import LocalDocker

        runtime = LocalDocker()
        client = workspace / "docker-client"
        runtime.isolate_client(client)
        runtime.env = {"PATH": os.defpath, "HOME": str(client)}
        return runtime

    def inspect_image(self, image):
        # Read-only image metadata; no container, pull or installation.
        from devharness_controlled.native_runtime import LocalDocker

        runtime = LocalDocker()
        identity = runtime._call(
            [
                "image",
                "inspect",
                image,
                "--format",
                '{"digests":{{json .RepoDigests}},"id":{{json .Id}},"os":{{json .Os}},'
                '"architecture":{{json .Architecture}},'
                '"labels":{{json .Config.Labels}}}',
            ],
            json_result=True,
        )
        labels = identity.get("labels") or {}
        try:
            inventory = json.loads(labels["devbench.community.inventory"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(
                "Installed image lacks an admitted community inventory"
            ) from error
        if "image" in inventory or image not in (identity["digests"] or []):
            raise ValueError(
                "Image-free installed inventory and verified external image "
                "digest required"
            )
        return {
            "repo_digests": identity["digests"],
            "image_id": identity["id"],
            "platform": identity["os"] + "/" + identity["architecture"],
            "inventory": {**inventory, "image": image},
        }

    @staticmethod
    def _inspect_owned(runtime, binding):
        identity = binding.get("container_id")
        selection = (
            "id=" + identity
            if identity
            else "name=^/" + re.escape(binding["container_name"]) + "$"
        )
        listed = runtime._call(
            [
                "container",
                "ls",
                "--all",
                "--no-trunc",
                "--filter",
                selection,
                "--format",
                "{{.ID}}",
            ]
        )
        ids = listed.splitlines()
        if not ids:
            return {
                **binding,
                "state": "missing",
                "observation": "successful_empty_daemon_listing",
            }
        if len(ids) != 1 or re.fullmatch("[0-9a-f]{64}", ids[0]) is None:
            raise ValueError("Ambiguous authoritative container identity observation")
        observed = runtime._call(
            ["inspect", ids[0], "--format", "{{json .}}"], json_result=True
        )
        labels = observed["Config"].get("Labels") or {}
        current = {
            "container_id": observed["Id"],
            "container_name": observed["Name"].removeprefix("/"),
            "image": observed["Config"]["Image"],
            "run_id": labels.get("devbench.run_id"),
            "inventory_sha256": labels.get("devbench.inventory_sha256"),
            "nextflow_session_id": labels.get("devbench.nextflow_session_id"),
            "profile": labels.get("devbench.profile"),
            "state": observed["State"]["Status"],
            "observation": "successful_daemon_listing_and_owned_inspection",
        }
        for key in _LIFECYCLE_KEYS:
            if current[key] != binding.get(key):
                raise PermissionError(
                    "Observed object is not the owned container/session identity "
                    "binding"
                )
        if (
            current["container_id"] != ids[0]
            or identity is not None
            and current["container_id"] != identity
        ):
            raise PermissionError("Observed immutable container identity changed")
        return {**binding, **current}

    def inspect_run(self, binding):
        from devharness_controlled.native_runtime import LocalDocker

        runtime = LocalDocker()
        if "output_dir" not in binding:
            return self._inspect_owned(runtime, binding)
        try:
            with self._control_lock(
                binding,
                LocalDockerExecutor._lifecycle_file(binding, "runtime-control.lock"),
                blocking=False,
            ):
                observed = self._inspect_owned(runtime, binding)
                phase = self._dispatch_state(binding)
                return {
                    **observed,
                    "dispatch_settled": self._cancellation_settled(
                        binding, observed, phase
                    ),
                    "dispatch_phase": phase.get("phase") if phase else "unknown",
                }
        except BlockingIOError:
            return {
                **self._inspect_owned(runtime, binding),
                "dispatch_settled": False,
                "dispatch_phase": "effect_in_flight",
            }

    @staticmethod
    def _lifecycle_file(binding, name):
        prefix = (
            "exploratory-"
            if binding.get("profile") == "exploratory-oci-1"
            else "community-"
        )
        return prefix + name

    @staticmethod
    @contextmanager
    def _control_lock(binding, name, *, blocking=True):
        output = _path(binding["output_dir"])
        with _path(output.parent / name).open("a+b") as lease:
            fcntl.flock(lease, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            yield

    @staticmethod
    def _cancel_intent(binding):
        path = _path(
            Path(binding["output_dir"]).parent
            / LocalDockerExecutor._lifecycle_file(binding, "cancel-intent")
        )
        try:
            with path.open("xb") as stream:
                stream.write(b"cancel")
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            pass

    @staticmethod
    def _dispatch_state(binding):
        path = _path(
            Path(binding["output_dir"]).parent
            / LocalDockerExecutor._lifecycle_file(binding, "dispatch-state.json")
        )
        if not path.exists():
            return None
        hash_artifact(path, max_bytes=4096, max_entries=1)
        value = json.loads(path.read_bytes())
        if any(value.get(key) != binding.get(key) for key in _LIFECYCLE_KEYS):
            raise PermissionError(
                "Dispatch phase belongs to a different immutable execution identity"
            )
        return value

    @staticmethod
    def _cancellation_settled(binding, observed, phase):
        # One predicate for cancel and read-only reconcile: a successful empty
        # listing cannot settle a create/start RPC that may still complete.
        if observed["state"] not in {"missing", "exited", "dead"} or phase is None:
            return False
        if phase.get("phase") in {
            "creating",
            "create_uncertain",
            "starting",
            "start_uncertain",
        }:
            return False
        return bool(
            phase.get("closed")
            or (
                Path(binding["output_dir"]).parent
                / LocalDockerExecutor._lifecycle_file(binding, "cancel-intent")
            ).exists()
        )

    @staticmethod
    def _dispatch_phase(binding, phase, **fields):
        # Caller holds the per-run effect lock. This is a lifecycle receipt,
        # never a permission grant or a replacement for the operation ledger.
        current = LocalDockerExecutor._dispatch_state(binding) or {
            key: binding.get(key) for key in _LIFECYCLE_KEYS
        }
        if current.get("phase") in {"cancelled", "removed"} and phase not in {
            "cancelled",
            "removed",
        }:
            phase = current["phase"]
        current.update(fields, phase=phase)
        path = _path(
            Path(binding["output_dir"]).parent
            / LocalDockerExecutor._lifecycle_file(binding, "dispatch-state.json")
        )
        pending = _path(path.with_suffix(".pending"))
        with pending.open("wb") as stream:
            stream.write(canonical_json(current))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, path)
        return current

    @staticmethod
    def _collection_receipt(binding, maximum):
        output = _path(binding["output_dir"])
        path = output.parent / LocalDockerExecutor._lifecycle_file(
            binding, "diagnostic-collection.json"
        )
        if not path.exists():
            return None
        hash_artifact(_path(path), max_bytes=65536, max_entries=1)
        record = json.loads(path.read_bytes())
        if not isinstance(record.get("files"), dict) or len(record["files"]) > 64:
            raise ValueError("Invalid bounded diagnostic collection receipt")
        keys = _COLLECTION_KEYS + (
            ("inventory_sha256", "profile") if binding.get("profile") else ()
        )
        if any(record.get(key) != binding.get(key) for key in keys):
            raise ValueError(
                "Prior diagnostic collection belongs to another immutable runtime"
            )
        total = 0
        for name, expected in record["files"].items():
            if len(PurePosixPath(name).parts) != 1 or name in {".", ".."}:
                raise ValueError("Invalid retained diagnostic path")
            actual = hash_artifact(
                _path(output / name), max_bytes=maximum, max_entries=1
            )
            total += actual["size_bytes"]
            if actual["sha256"] != expected or total > maximum:
                raise ValueError("Retained diagnostic bytes changed")
        return {**record, "reused_previously_collected_bytes": True}

    def _collect(self, runtime, binding, maximum):
        output = _path(binding["output_dir"])
        with self._control_lock(
            binding, LocalDockerExecutor._lifecycle_file(binding, "output-copy.lock")
        ):
            retained = self._collection_receipt(binding, maximum)
            if retained is not None:
                return retained
            temporary = output.parent / ("collection-" + uuid.uuid4().hex)
            temporary.mkdir(mode=0o700)
            try:
                command = runtime.command(
                    [
                        "exec",
                        binding["container_id"],
                        "/usr/bin/env",
                        "-i",
                        "PATH=/usr/local/bin:/usr/bin:/bin",
                        "tar",
                        "-C",
                        "/outputs",
                        "-cf",
                        "-",
                        ".",
                    ]
                )
                self._collect_archive(runtime, binding, command, temporary, maximum)
                files = {}
                for path in temporary.iterdir():
                    expected = hash_artifact(path, max_bytes=maximum, max_entries=1)[
                        "sha256"
                    ]
                    destination = output / path.name
                    if destination.exists():
                        if (
                            hash_artifact(
                                _path(destination), max_bytes=maximum, max_entries=1
                            )["sha256"]
                            != expected
                        ):
                            raise ValueError(
                                "Concurrent output collection changed captured bytes"
                            )
                    else:
                        os.link(path, destination)
                    files[path.name] = expected
                keys = _COLLECTION_KEYS + (
                    ("inventory_sha256", "profile") if binding.get("profile") else ()
                )
                record = {key: binding.get(key) for key in keys}
                record.update(
                    status="collected_while_supervisor_live",
                    files=files,
                    reused_previously_collected_bytes=False,
                )
                receipt = output.parent / LocalDockerExecutor._lifecycle_file(
                    binding, "diagnostic-collection.json"
                )
                with receipt.open("xb") as stream:
                    stream.write(canonical_json(record))
                    stream.flush()
                    os.fsync(stream.fileno())
                return record
            finally:
                shutil.rmtree(temporary)

    @staticmethod
    def _collect_archive(runtime, binding, command, temporary, maximum):
        if binding.get("profile") == "exploratory-oci-1" and maximum > 16_000_000:
            with tempfile.TemporaryFile(dir=temporary) as spool:
                code, _, error = _bounded(
                    command, runtime.env, 5, maximum + 1024**2, stdout_sink=spool
                )
                if code:
                    raise ValueError(
                        "Unable to collect live sandbox tmpfs: "
                        + error.decode("utf-8", "replace")[:1000]
                    )
                spool.seek(0)
                _extract(spool, temporary, maximum)
        else:
            code, archive, error = _bounded(command, runtime.env, 5, maximum + 1024**2)
            if code:
                raise ValueError(
                    "Unable to collect live sandbox tmpfs: "
                    + error.decode("utf-8", "replace")[:1000]
                )
            _extract(archive, temporary, maximum)

    def cancel_run(self, binding):
        from devharness_controlled.native_runtime import LocalDocker

        runtime = LocalDocker()
        observed = self._inspect_owned(runtime, binding)
        binding = {**binding, **observed}
        # Intent can be recorded while an RPC owns the effect lock, but such a
        # request remains PENDING until that in-flight effect is settled.
        self._cancel_intent(binding)
        try:
            with self._control_lock(
                binding,
                LocalDockerExecutor._lifecycle_file(binding, "runtime-control.lock"),
                blocking=False,
            ):
                return self._cancel_settled(runtime, binding)
        except BlockingIOError:
            return {
                **observed,
                "state": "cancellation_pending",
                "diagnostic_status": "dispatch_effect_in_flight",
            }

    def _cancel_settled(self, runtime, binding):
        observed = self._inspect_owned(runtime, binding)
        binding = {**binding, **observed}
        phase = self._dispatch_state(binding)
        if observed["state"] == "missing":
            if not self._cancellation_settled(binding, observed, phase):
                return {
                    **observed,
                    "state": "cancellation_pending",
                    "diagnostic_status": (
                        "create_effect_not_settled; positive absence is not terminal "
                        "proof"
                    ),
                }
            self._dispatch_phase(
                binding,
                "cancelled",
                closed=True,
                in_flight=False,
                cleanup_complete=True,
            )
            retained = (
                self._collection_receipt(binding, binding["max_output_bytes"])
                if binding.get("container_id")
                else None
            )
            return {
                **observed,
                "state": "cancelled",
                "diagnostic_collection": retained,
                "diagnostic_loss": (
                    None
                    if retained is not None
                    else "object_absent; no uncollected tmpfs bytes are claimed"
                ),
            }
        if observed["state"] == "created":
            runtime._call(["rm", "--force", "--volumes", binding["container_id"]])
            self._dispatch_phase(
                binding,
                "cancelled",
                closed=True,
                in_flight=False,
                cleanup_complete=True,
            )
            return {
                **observed,
                "state": "cancelled",
                "diagnostic_status": "engine_never_started",
            }
        if observed["state"] in {"exited", "dead"}:
            return self._cancel_stopped(runtime, binding, observed)
        if phase is not None and not phase.get("worker_reserved", False):
            # No worker effect has been reserved. The durable latch plus this
            # same effect lock prevents any subsequent start/exec phase.
            runtime._call(["stop", "--time=1", binding["container_id"]], timeout=5)
            stopped = self._inspect_owned(runtime, binding)
            if stopped["state"] not in {"exited", "dead", "missing"}:
                raise ValueError("Pre-worker supervisor stop was not verified")
            self._dispatch_phase(binding, "cancelled", closed=True, in_flight=False)
            return {
                **stopped,
                "state": "cancelled",
                "diagnostic_status": "worker_never_launched",
            }
        if binding.get("profile") == "exploratory-oci-1":
            return self._cancel_live_exploratory(runtime, binding)
        # Request an attached worker to stop its owned engine process group.
        # PID 1 stays live until worker diagnostics have been finalized/copied.
        runtime._call(
            [
                "exec",
                binding["container_id"],
                "python3",
                "-c",
                (
                    "from pathlib import Path;Path('/tmp/community/cancel.request').wr"
                    "ite_text('cancel')"
                ),
            ],
            timeout=5,
        )
        wait_code = """import json,time
from pathlib import Path
p=Path('/tmp/community/worker-state.json');deadline=time.monotonic()+5
while time.monotonic()<deadline:
    if p.is_file():
        value=json.loads(p.read_bytes())
        if value.get('state') in ('finished','cancelled'):
            print(json.dumps(value));break
    time.sleep(.05)
else:print(json.dumps({'state':'cancellation_pending'}))
"""
        finished = runtime._call(
            ["exec", binding["container_id"], "python3", "-c", wait_code],
            json_result=True,
            timeout=6,
        )
        if finished.get("state") not in {"finished", "cancelled"}:
            return {
                **observed,
                "state": "cancellation_pending",
                "diagnostic_status": "worker_finalization_not_yet_observed",
            }
        collection = self._collect(runtime, binding, binding["max_output_bytes"])
        runtime._call(["stop", "--time=1", binding["container_id"]], timeout=5)
        stopped = self._inspect_owned(runtime, binding)
        if stopped["state"] not in {"exited", "dead", "missing"}:
            raise ValueError("Owned supervisor stop was not verified")
        self._dispatch_phase(binding, "cancelled", closed=True, in_flight=False)
        return {**stopped, "state": "cancelled", "diagnostic_collection": collection}

    def _cancel_stopped(self, runtime, binding, observed):
        retained = self._collection_receipt(binding, binding["max_output_bytes"])
        removed = False
        if binding.get("profile") == "exploratory-oci-1":
            runtime._call(
                ["rm", "--force", "--volumes", binding["container_id"]], timeout=5
            )
            removed = self._inspect_owned(runtime, binding)["state"] == "missing"
            if not removed:
                raise ValueError(
                    "Stopped exploratory object removal could not be verified"
                )
        self._dispatch_phase(
            binding,
            "cancelled",
            closed=True,
            in_flight=False,
            **({"cleanup_complete": True} if removed else {}),
        )
        return {
            **observed,
            "state": "cancelled",
            "diagnostic_collection": retained,
            "diagnostic_loss": None
            if retained is not None
            else "stopped_container_tmpfs_unavailable",
        }

    def _cancel_live_exploratory(self, runtime, binding):
        collection, loss = None, None
        try:
            collection = self._collect(runtime, binding, binding["max_output_bytes"])
        except (
            OSError,
            ValueError,
            TimeoutError,
            subprocess.TimeoutExpired,
        ) as error:
            loss = str(error)[:1000]
        runtime._call(
            ["rm", "--force", "--volumes", binding["container_id"]], timeout=5
        )
        stopped = self._inspect_owned(runtime, binding)
        if stopped["state"] != "missing":
            raise ValueError("Exploratory container removal could not be verified")
        self._dispatch_phase(
            binding,
            "cancelled",
            closed=True,
            in_flight=False,
            cleanup_complete=True,
        )
        return {
            **stopped,
            "state": "cancelled",
            "diagnostic_collection": collection,
            "diagnostic_loss": loss,
        }

    @staticmethod
    def _check_exploratory_runtime(runtime, binding, limits, workspace):
        value = runtime._call(
            ["inspect", binding["container_id"], "--format", "{{json .}}"],
            json_result=True,
        )
        host, config = value["HostConfig"], value["Config"]
        uid, gid = _container_ids()
        expected_tmpfs = {
            target: sorted(_tmpfs_options(size, uid, gid).split(","))
            for target, size in _tmpfs_sizes(limits).items()
        }
        observed_tmpfs = host.get("Tmpfs")
        tmpfs_matches = (
            isinstance(observed_tmpfs, dict)
            and all(isinstance(options, str) for options in observed_tmpfs.values())
            and {
                target: sorted(options.split(","))
                for target, options in observed_tmpfs.items()
            }
            == expected_tmpfs
        )
        expected_limits = {**_BASE_ULIMITS, "fsize": limits["max_output_bytes"]}
        ulimits = host.get("Ulimits")
        ulimits_match = (
            isinstance(ulimits, list)
            and len(ulimits) == len(expected_limits)
            and all(
                isinstance(item, dict)
                and isinstance(item.get("Name"), str)
                and type(item.get("Soft")) is int
                and type(item.get("Hard")) is int
                for item in ulimits
            )
            and {item["Name"]: (item["Soft"], item["Hard"]) for item in ulimits}
            == {name: (size, size) for name, size in expected_limits.items()}
        )
        restart = host.get("RestartPolicy")
        restart_disabled = (
            isinstance(restart, dict)
            and restart.get("Name") == "no"
            and type(restart.get("MaximumRetryCount")) is int
            and restart["MaximumRetryCount"] == 0
        )
        expected_mounts = {
            ("/inputs", str(workspace / "inputs")),
            ("/method", str(workspace / "method")),
        }
        mounts = {
            (item["Destination"], item["Source"])
            for item in value["Mounts"]
            if item["Type"] == "bind"
            and item["RW"] is False
            and item.get("Propagation") == "rprivate"
        }
        # Older inspect responses can omit configured bind options. An
        # options object, when present, must retain the requested nonrecursive
        # setting (Docker omits false from the JSON representation).
        configured_mounts = host.get("Mounts")
        configured_mounts_match = configured_mounts is None or (
            isinstance(configured_mounts, list)
            and len(configured_mounts) == len(expected_mounts)
            and all(
                isinstance(item, dict)
                and item.get("Type") == "bind"
                and item.get("ReadOnly") is True
                and isinstance(item.get("Target"), str)
                and isinstance(item.get("Source"), str)
                and (
                    item.get("BindOptions") is None
                    or (
                        isinstance(item["BindOptions"], dict)
                        and item["BindOptions"].get("Propagation", "rprivate")
                        in {"", "rprivate"}
                        and item["BindOptions"].get("NonRecursive") is True
                    )
                )
                for item in configured_mounts
            )
            and {(item["Target"], item["Source"]) for item in configured_mounts}
            == expected_mounts
        )
        if (
            value["Image"] != EXPLORATORY_IMAGE_ID
            or config["Entrypoint"] != ["/bin/sleep"]
            or config["Cmd"] != [str(limits["timeout_seconds"] + 8)]
            or config["User"] != f"{uid}:{gid}"
            or host["NetworkMode"] != "none"
            or not host["ReadonlyRootfs"]
            or host["Privileged"]
            or host["CapDrop"] != ["ALL"]
            or host.get("CapAdd")
            or "no-new-privileges:true" not in (host["SecurityOpt"] or [])
            or host["NanoCpus"] != round(limits["cpu"] * 1e9)
            or host["Memory"] != limits["memory_mb"] * 1024**2
            or host["MemorySwap"] != host["Memory"]
            or host["PidsLimit"] != limits["pids"]
            or host["LogConfig"]["Type"] != "none"
            or host["IpcMode"] != "private"
            or host.get("PidMode") not in {"", "private"}
            or not tmpfs_matches
            or not ulimits_match
            or not restart_disabled
            or not configured_mounts_match
            or mounts != expected_mounts
            or len(value["Mounts"]) != len(expected_mounts)
        ):
            raise PermissionError(
                "Docker did not retain the frozen exploratory isolation controls"
            )
        return hashlib.sha256(
            canonical_json(
                {
                    "image_id": value["Image"],
                    "binds": [list(mount) for mount in sorted(mounts)],
                    "tmpfs": host["Tmpfs"],
                    "cpu": host["NanoCpus"],
                    "memory": host["Memory"],
                    "pids": host["PidsLimit"],
                    "security": host["SecurityOpt"],
                    "cmd": config["Cmd"],
                    "ulimits": ulimits,
                    "restart_policy": restart,
                    "bind_options": configured_mounts,
                }
            )
        ).hexdigest()

    def execute(
        self, contract, inputs, parameters, source, lock, output_dir, *, frozen=None
    ):
        # Registration and consumption are local to the caller's context. The
        # captured callback stays stable across threads and nested calls.
        observer = self._observer_context.get()
        self._observer_context.set(None)
        output_dir = _path(output_dir)
        workspace = output_dir.parent
        runtime, identity = self._prepare_runtime(
            contract, inputs, parameters, workspace, frozen
        )
        self._stage_method(
            workspace, inputs, parameters, source, lock, contract, frozen
        )
        exploratory = frozen is not None
        community = (
            contract.get("method_id") == "community.nfcore.differentialabundance.2"
        )
        durable = community or exploratory
        binding, labels = self._runtime_binding(
            contract, output_dir, lock, frozen, community
        )

        def emit(event):
            if (
                durable
                and callable(observer)
                and observer({"event": event, **binding}) is False
            ):
                self._cancel_intent(binding)

        primary = None
        try:
            if not self._create_container(
                runtime,
                identity,
                binding,
                contract,
                workspace,
                labels,
                emit,
                durable,
                exploratory,
            ):
                return self._cancelled_before_worker(binding)
            controls = (
                self._check_exploratory_runtime(
                    runtime, binding, contract["execution"], workspace
                )
                if exploratory
                else None
            )
            emit("container_created")
            if not self._start_worker(runtime, binding, emit, durable):
                return self._cancelled_before_worker(binding)
            return self._run_worker(
                runtime,
                identity,
                binding,
                contract,
                output_dir,
                controls,
                emit,
                community,
                exploratory,
            )
        except (Exception, KeyboardInterrupt, SystemExit) as error:
            primary = error
            raise
        finally:
            if binding.get("container_id"):
                self._cleanup_container(runtime, binding, durable, exploratory, primary)

    def _prepare_runtime(self, contract, inputs, parameters, workspace, frozen):
        exploratory = frozen is not None
        if exploratory:
            from .methods import exploratory_method

            method = exploratory_method(contract)
            if (
                frozen["method_version_id"] != method.version_id
                or frozen["parameters_sha256"]
                != hashlib.sha256(canonical_json(parameters)).hexdigest()
                or set(inputs) != set(frozen["input_sha256"])
            ):
                raise PermissionError(
                    "Exploratory inputs differ from the frozen invocation"
                )
        runtime = self._runtime(workspace)
        identity = runtime._call(
            [
                "image",
                "inspect",
                contract["environment"]["image"],
                "--format",
                '{"id":{{json .Id}},"digests":{{json .RepoDigests}},'
                '"volumes":{{json (index .Config "Volumes")}},"os":{{json .Os}}}',
            ],
            json_result=True,
        )
        if (
            contract["environment"]["image"] not in (identity["digests"] or [])
            or identity["os"] != "linux"
            or len(identity["volumes"] or {}) > 16
        ):
            raise ValueError("Installed image identity is unsupported or mismatched")
        if exploratory and (
            identity["id"] != EXPLORATORY_IMAGE_ID or identity["volumes"]
        ):
            raise PermissionError(
                "Exploratory supervisor requires the admitted image ID without "
                "image volumes"
            )
        return runtime, identity

    @staticmethod
    def _stage_method(workspace, inputs, parameters, source, lock, contract, frozen):
        exploratory = frozen is not None
        (workspace / "inputs").mkdir(mode=0o755)
        (workspace / "method").mkdir(mode=0o755)
        for name, path in inputs.items():
            captured = copy_artifact(
                path, workspace / "inputs" / name, max_bytes=100_000_000, max_entries=1
            )
            if exploratory and (
                captured["kind"] != "file"
                or captured["sha256"] != frozen["input_sha256"][name]
            ):
                raise PermissionError(
                    "Staged exploratory input differs from the frozen artifact"
                )
        method = copy_artifact(
            source, workspace / "method" / "main.py", max_bytes=1_000_000, max_entries=1
        )
        environment = copy_artifact(
            lock,
            workspace / "method" / "environment.lock",
            max_bytes=1_000_000,
            max_entries=1,
        )
        if exploratory and (
            method["kind"] != "file"
            or method["sha256"] != contract["implementation"]["source_sha256"]
            or environment["kind"] != "file"
            or environment["sha256"] != contract["environment"]["lock_sha256"]
        ):
            raise PermissionError(
                "Staged exploratory source or lock differs from the registered method"
            )
        (workspace / "method" / "parameters.json").write_bytes(
            canonical_json(parameters)
        )
        for directory in (workspace / "inputs", workspace / "method"):
            directory.chmod(0o755)
            for path in directory.iterdir():
                path.chmod(0o444)

    @staticmethod
    def _runtime_binding(contract, output_dir, lock, frozen, community):
        workspace = output_dir.parent
        exploratory = frozen is not None
        name = "devharness-method-" + workspace.name
        labels = {}
        binding = {
            "run_id": workspace.name,
            "container_name": name,
            "image": contract["environment"]["image"],
        }
        if community:
            inventory = json.loads(Path(lock).read_bytes())
            binding.update(
                output_dir=str(output_dir),
                max_output_bytes=contract["execution"]["max_output_bytes"],
                inventory_sha256=hashlib.sha256(canonical_json(inventory)).hexdigest(),
                nextflow_session_id=str(uuid.uuid4()),
            )
            labels = {
                "devbench." + key: binding[key]
                for key in ("run_id", "inventory_sha256", "nextflow_session_id")
            }
        if exploratory:
            binding.update(
                output_dir=str(output_dir),
                max_output_bytes=contract["execution"]["max_output_bytes"],
                inventory_sha256=hashlib.sha256(canonical_json(frozen)).hexdigest(),
                nextflow_session_id=uuid.uuid4().hex,
                profile="exploratory-oci-1",
            )
            labels = {
                "devbench." + key: binding[key]
                for key in (
                    "run_id",
                    "inventory_sha256",
                    "nextflow_session_id",
                    "profile",
                )
            }
        return binding, labels

    def _effect_lock(self, binding, durable):
        if not durable:
            return nullcontext()
        return self._control_lock(
            binding, self._lifecycle_file(binding, "runtime-control.lock")
        )

    def _latched(self, binding):
        directory = Path(binding["output_dir"]).parent
        return (directory / self._lifecycle_file(binding, "cancel-intent")).exists()

    def _cancelled_before_worker(self, binding):
        # The caller no longer holds the effect lock. Take it again before
        # updating durable dispatch state so cancellation and cleanup serialize.
        with self._effect_lock(binding, True):
            self._dispatch_phase(binding, "cancelled", closed=True, in_flight=False)
        return {
            "exit_code": 143,
            "worker_exit_code": None,
            "worker_started": False,
            "runtime": "local_docker",
            "engine_started": False,
            "engine_exit_code": None,
            "engine_outcome": "not_started",
            "cancelled": True,
            "container_name": binding["container_name"],
            "container_id": binding.get("container_id"),
            "stdout": "",
            "stderr": "Dispatch cancelled before worker launch",
            "diagnostic_status": "worker_never_launched",
        }

    def _record_interrupted_effect(self, binding, phase, error):
        try:
            self._dispatch_phase(binding, phase, in_flight=False, closed=True)
        except (OSError, ValueError, RuntimeError) as observation_error:
            # The existing in-flight marker still blocks false confirmation
            # of noncompletion if its update cannot be written.
            error.add_note(
                f"Docker phase receipt failed: {type(observation_error).__name__}"
            )

    def _create_container(
        self,
        runtime,
        identity,
        binding,
        contract,
        workspace,
        labels,
        emit,
        durable,
        exploratory,
    ):
        if durable:
            with self._effect_lock(binding, True):
                self._dispatch_phase(
                    binding,
                    "create_intent",
                    in_flight=False,
                    closed=False,
                    worker_reserved=False,
                )
        emit("container_create_intent")
        with self._effect_lock(binding, durable):
            if durable and self._latched(binding):
                return False
            if durable:
                self._dispatch_phase(binding, "creating", in_flight=True)
            try:
                container_id = runtime._call(
                    container_command(
                        contract,
                        workspace,
                        binding["container_name"],
                        identity["volumes"] or {},
                        labels=labels,
                        **({"exploratory": True} if exploratory else {}),
                    )
                )
                container_id = str(container_id).strip()
                if durable and re.fullmatch("[0-9a-f]{64}", container_id) is None:
                    raise ValueError(
                        "Create response did not identify an immutable owned container"
                    )
            except (Exception, KeyboardInterrupt, SystemExit) as error:
                if durable:
                    self._record_interrupted_effect(binding, "create_uncertain", error)
                raise
            binding["container_id"] = container_id
            if durable:
                self._dispatch_phase(binding, "created", in_flight=False)
        return True

    def _start_worker(self, runtime, binding, emit, durable):
        emit("container_start_intent")
        with self._effect_lock(binding, durable):
            if durable and self._latched(binding):
                return False
            if durable:
                self._dispatch_phase(binding, "starting", in_flight=True)
            try:
                runtime._call(["start", binding["container_id"]])
            except (Exception, KeyboardInterrupt, SystemExit) as error:
                if durable:
                    self._record_interrupted_effect(binding, "start_uncertain", error)
                raise
            if durable:
                self._dispatch_phase(binding, "started", in_flight=False)
        emit("container_started")
        if durable:
            with self._effect_lock(binding, True):
                if self._latched(binding):
                    return False
                self._dispatch_phase(
                    binding, "exec_intent", in_flight=False, worker_reserved=True
                )
            emit("worker_exec_intent")
            with self._effect_lock(binding, True):
                if self._latched(binding):
                    return False
                # An exec RPC may still be in flight after the lock is released.
                self._dispatch_phase(
                    binding, "executing", in_flight=True, worker_reserved=True
                )
        return True

    def _run_worker(
        self,
        runtime,
        identity,
        binding,
        contract,
        output_dir,
        controls,
        emit,
        community,
        exploratory,
    ):
        durable = community or exploratory
        result = None
        execution_error = None
        command = runtime.command(
            [
                "exec",
                binding["container_id"],
                "/usr/bin/env",
                "-i",
                "PATH=/usr/local/bin:/usr/bin:/bin",
                "HOME=/tmp",
                "TMPDIR=/tmp",
                "PYTHONDONTWRITEBYTECODE=1",
                "OMP_NUM_THREADS=1",
                *(
                    ["DEVBENCH_NEXTFLOW_SESSION_ID=" + binding["nextflow_session_id"]]
                    if community
                    else []
                ),
                *(
                    ["/usr/local/bin/python3", "-I", "-B", "/method/main.py"]
                    if exploratory
                    else contract["implementation"]["entrypoint"]
                ),
            ]
        )
        try:
            code, stdout, stderr = _bounded(
                command,
                runtime.env,
                contract["execution"]["timeout_seconds"],
                1_000_000,
            )
            if durable:
                with self._effect_lock(binding, durable):
                    self._dispatch_phase(
                        binding,
                        "worker_finished",
                        in_flight=False,
                        closed=True,
                        worker_completed=True,
                    )
            result = {
                "exit_code": code,
                "runtime": "local_docker",
                "image_id": identity["id"],
                "image": contract["environment"]["image"],
                "stdout": stdout.decode("utf-8", "replace"),
                "stderr": stderr.decode("utf-8", "replace"),
                "container_name": binding["container_name"],
                "container_id": binding["container_id"],
            }
            if exploratory:
                result.update(
                    controls_sha256=controls, frozen_sha256=binding["inventory_sha256"]
                )
            if community:
                result.update(
                    engine_started=None, engine_exit_code=None, engine_outcome="unknown"
                )
        except (TimeoutError, ValueError, subprocess.TimeoutExpired) as error:
            if durable:
                with self._effect_lock(binding, durable):
                    self._record_interrupted_effect(binding, "exec_uncertain", error)
            execution_error = error
        execution_error = self._collect_after_worker(
            runtime,
            binding,
            contract,
            output_dir,
            emit,
            result,
            execution_error,
            community,
            durable,
        )
        if execution_error is not None:
            raise execution_error
        if exploratory and self._latched(binding):
            result.update(exit_code=143, cancelled=True)
        return result

    def _collect_after_worker(
        self,
        runtime,
        binding,
        contract,
        output_dir,
        emit,
        result,
        execution_error,
        community,
        durable,
    ):
        # Community diagnostics survive failed task exit, while the caller
        # still rejects all successful publication on any nonzero result.
        if durable or (result is not None and result["exit_code"] == 0):
            maximum = contract["execution"]["max_output_bytes"]
            try:
                if durable:
                    collection = self._collect(runtime, binding, maximum)
                    if result is not None:
                        result["diagnostic_collection"] = collection
                else:
                    code, archive, error = _bounded(
                        runtime.command(
                            [
                                "exec",
                                binding["container_id"],
                                "/usr/bin/env",
                                "-i",
                                "PATH=/usr/local/bin:/usr/bin:/bin",
                                "tar",
                                "-C",
                                "/outputs",
                                "-cf",
                                "-",
                                ".",
                            ]
                        ),
                        runtime.env,
                        30,
                        maximum + 1024**2,
                    )
                    if code:
                        raise ValueError(
                            "Unable to collect sandbox outputs: "
                            + error.decode("utf-8", "replace")[:1000]
                        )
                    _extract(archive, output_dir, maximum)
                tasks = output_dir / "task-evidence.json"
                if community and tasks.is_file():
                    self._observe_community_worker(binding, tasks, result, emit)
            except (TimeoutError, ValueError, subprocess.TimeoutExpired) as error:
                if result is not None:
                    error.execution = {
                        **result,
                        "diagnostic_collection_error": str(error)[:2000],
                        "diagnostic_collection": {
                            "status": "unavailable",
                            "reason": (
                                "No recovery of uncollected tmpfs bytes is claimed"
                            ),
                            "error": str(error)[:1000],
                        },
                    }
                elif execution_error is not None and hasattr(
                    execution_error, "execution"
                ):
                    execution_error.execution["diagnostic_collection_error"] = str(
                        error
                    )[:2000]
                    execution_error.execution["diagnostic_collection"] = {
                        "status": "unavailable",
                        "reason": (
                            "Supervisor deadline, interruption, or collection "
                            "failure may have lost tmpfs; "
                            "bytes were not recovered"
                        ),
                    }
                if execution_error is None:
                    execution_error = error
        return execution_error

    @staticmethod
    def _observe_community_worker(binding, tasks, result, emit):
        task_record = json.loads(tasks.read_bytes())
        state = task_record.get("engine_state", {})
        if result is not None:
            result.update(
                engine_started=state.get("engine_started"),
                engine_exit_code=state.get("engine_exit_code"),
                engine_outcome=state.get("outcome", "unknown"),
                cancelled=task_record.get("worker_exit_code") == 143,
            )
        if (
            task_record.get("nextflow_session_id") == binding["nextflow_session_id"]
            and task_record.get("engine_session_observed") is True
        ):
            emit("engine_session_started")

    def _cleanup_container(self, runtime, binding, durable, exploratory, primary):
        # Only a successful create response provides immutable ownership.
        try:
            with self._effect_lock(binding, durable):
                if durable and self._dispatch_state(binding).get(
                    "cleanup_complete", False
                ):
                    return
                runtime._call(["rm", "--force", "--volumes", binding["container_id"]])
                if (
                    exploratory
                    and self._inspect_owned(runtime, binding)["state"] != "missing"
                ):
                    raise ValueError("Exploratory cleanup could not be verified")
                if durable:
                    self._dispatch_phase(
                        binding,
                        "removed",
                        closed=True,
                        in_flight=False,
                        cleanup_complete=True,
                    )
        except Exception as cleanup_error:
            if primary is None:
                raise
            primary.add_note(f"Docker cleanup failed: {type(cleanup_error).__name__}")
            details = getattr(primary, "execution", None)
            if isinstance(details, dict):
                details["cleanup_error"] = type(cleanup_error).__name__
