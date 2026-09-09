"""Fixed, local Docker transport for the unqualified native NB candidate.

No image downloads, remote daemons, user commands, model calls or host credentials
are admitted. Docker and the coordinator host remain trusted infrastructure; the
retained daemon record is evidence, not remote attestation of execution.
"""
from __future__ import annotations

import hashlib
from contextlib import nullcontext
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import time
import uuid

from devharness_core.provenance import hash_artifact
from .contracts import canonical_json_bytes, parse_json_bytes
from .executor import execution_identity, THREAD_VARIABLES

NATIVE_IMAGE_ID = "sha256:c34c4ecaf9f1ef909b0c2478f5643530e9dec02c9db9cb8d05f406b9208d8b0e"
DRIVER = Path(__file__).resolve().parent.parent / "devharness_server/r/native_nb.R"
PACKAGE_LOCK = Path(__file__).resolve().parent / "native-package-sources.lock.json"
PROFILE_VERSION = "native_container_profile/2"
SUPPORTED_PROFILES = {"native_container_profile/1", PROFILE_VERSION}
LOG_LIMIT = 1024 * 1024
RESULT_LIMIT = 16 * 1024 * 1024
COMMAND = ["--vanilla", "/input/native_nb.R", "--config", "/input/request.json",
           "--output", "/output/result.json"]
IMAGE_ENV = (
    "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "R_VERSION=4.6.1",
    "R_HOME=/usr/local/lib/R", "TZ=Etc/UTC", "CRAN=https://p3m.dev/cran/__linux__/noble/latest",
    "LANG=en_US.UTF-8", "S6_VERSION=v2.1.0.2", "RSTUDIO_VERSION=2026.07.1+147", "DEFAULT_USER=rstudio",
    "BIOCONDUCTOR_USE_CONTAINER_REPOSITORY=TRUE", "TARGETARCH=amd64", "TARGETPLATFORM=linux/amd64",
    "PLATFORM=linux/amd64", "LIBSBML_CFLAGS=-I/usr/include", "LIBSBML_LIBS=-lsbml",
    "BIOCONDUCTOR_DOCKER_VERSION=3.23.4", "BIOCONDUCTOR_VERSION=3.23", "BIOCONDUCTOR_NAME=bioconductor_docker",
    "R_LIBS_USER=/opt/devharness-native/R-library", "OMP_NUM_THREADS=1", "OPENBLAS_NUM_THREADS=1",
    "MKL_NUM_THREADS=1", "MAKEFLAGS=-j2")
CLIENT_CONFIG = {"auths": {}, "proxies": {}}


def approved_environment():
    env = dict(entry.split("=", 1) for entry in IMAGE_ENV)
    env.update({key: "1" for key in THREAD_VARIABLES})
    return [key + "=" + value for key, value in env.items()]


def _redact_unapproved_environment(entries):
    approved = set(approved_environment())
    return [entry if entry in approved else entry.split("=", 1)[0] + "=<unapproved-redacted>" for entry in entries]


class NativeRuntimeError(ValueError):
    """Runtime identity, isolation, transport or cleanup could not be established."""


def _check_live_outputs(directory):
    members = list(Path(directory).iterdir())
    _require(len(members) <= 2, "Native output/log limit exceeded: more than two output names")
    for path in members:
        try:
            details = path.lstat()
        except FileNotFoundError:
            # The approved driver unlinks its temporary hard-link name after
            # publishing result.json. A directory listing is not a snapshot.
            _require(path.name.startswith(".native-nb-"), "Native output disappeared: " + path.name)
            continue
        _require(stat.S_ISREG(details.st_mode) and details.st_size <= RESULT_LIMIT,
                 "Native output/log limit exceeded: nonregular or oversized entry " + path.name)


def _require(condition, message):
    if not condition:
        raise NativeRuntimeError(message)


def _digest(value):
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def local_endpoint(endpoint):
    _require(type(endpoint) is str and endpoint.startswith("unix:///")
             and not any(ord(c) < 32 or ord(c) == 127 for c in endpoint),
             "Only an explicit local Unix Docker endpoint is admitted")
    _require(Path(endpoint[7:]).is_absolute(), "Docker socket path must be absolute")
    return endpoint


def _directory(value):
    path = Path(value)
    _require(path.is_absolute() and path.resolve() == path and path.is_dir(),
             "Container staging requires an existing absolute non-symlink directory")
    _require(not any(c == "," or ord(c) < 32 or ord(c) == 127 for c in str(path)),
             "Unsupported character in container mount path")
    return str(path)


def container_profile(plan, inputs, outputs, *, uid=None, gid=None):
    _require(plan.runtime_image_id == NATIVE_IMAGE_ID and plan.platform == "linux/amd64",
             "Native runtime must be the approved immutable image and platform")
    uid = (os.geteuid() or 65532) if uid is None else uid
    gid = (os.getegid() if os.geteuid() else 65532) if gid is None else gid
    _require(type(uid) is int and uid > 0 and type(gid) is int and gid >= 0, "Nonroot numeric container user required")
    return {"schema_version": PROFILE_VERSION, "image_id": NATIVE_IMAGE_ID,
            "platform": "linux/amd64", "user": f"{uid}:{gid}",
            "input_dir": _directory(inputs), "output_dir": _directory(outputs),
            "memory_bytes": plan.max_memory_mb * 1024**2, "nano_cpus": plan.max_cpus * 10**9,
            "pids_limit": 64, "timeout_sec": plan.timeout_sec,
            "tmpfs": "rw,nosuid,nodev,noexec,size=67108864,mode=1777",
            "threads": list(THREAD_VARIABLES), "log_limit_bytes": LOG_LIMIT,
            "result_limit_bytes": RESULT_LIMIT}


def create_arguments(profile, plan_sha256, name):
    _require(re.fullmatch(r"[a-z0-9-]+", name) and name.startswith("devharness-native-"), "Invalid owned container name")
    _require(re.fullmatch(r"[0-9a-f]{64}", plan_sha256), "Invalid container plan label")
    entrypoint, command = container_invocation(profile)
    args = ["create", "--pull=never", "--name", name, "--platform", profile["platform"],
            "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges=true",
            "--pids-limit", str(profile["pids_limit"]), "--memory", str(profile["memory_bytes"]),
            "--memory-swap", str(profile["memory_bytes"]), "--cpus", str(profile["nano_cpus"] // 10**9),
            "--user", profile["user"], "--ipc", "private", "--tmpfs", "/tmp:" + profile["tmpfs"],
            "--log-driver", "none", "--label", "io.devharness.plan-sha256=" + plan_sha256,
            "--mount", "type=bind,src=" + profile["input_dir"] + ",dst=/input,readonly",
            "--mount", "type=bind,src=" + profile["output_dir"] + ",dst=/output",
            "--entrypoint", entrypoint]
    for key in profile["threads"]:
        args.extend(["--env", key + "=1"])
    return args + [profile["image_id"], *command]


def container_invocation(profile):
    """Preserve profile1 evidence; new runs have an independent container timer."""
    _require(profile.get("schema_version") in SUPPORTED_PROFILES, "Unsupported native container profile")
    if profile["schema_version"] == "native_container_profile/1":
        return "Rscript", list(COMMAND)
    duration = profile["timeout_sec"]
    _require(type(duration) is int and 1 <= duration <= 86400, "Invalid native deadline")
    # timeout stays in the container process namespace if the host coordinator
    # dies. KILL avoids a grace period beyond the signed maximum. A 137 exit is
    # retained as a process failure, never alone interpreted as OOM or timeout.
    return "/usr/bin/timeout", ["--signal=KILL", f"{duration}s", "Rscript", *COMMAND]


def check_container_record(record, profile, plan_sha256, name):
    """Check actual daemon state against the prescribed profile before starting."""
    _require(all(type(profile.get(key)) is int and profile[key] > 0 for key in
                 ("memory_bytes", "nano_cpus", "pids_limit", "timeout_sec", "log_limit_bytes", "result_limit_bytes")),
             "Invalid native profile numeric bounds")
    _require(type(record) is dict and re.fullmatch(r"[0-9a-f]{64}", record.get("Id", "")), "Invalid container ID")
    _require(record.get("Name") == "/" + name and record.get("Image") == profile["image_id"], "Wrong container identity")
    config, host = record["Config"], record["HostConfig"]
    _require(config.get("User") == profile["user"] and int(profile["user"].split(":")[0]) > 0,
             "Unexpected container user")
    entrypoint, command = container_invocation(profile)
    _require(config.get("Entrypoint") == [entrypoint] and config.get("Cmd") == command,
             "Unexpected native command")
    _require(config.get("Labels", {}).get("io.devharness.plan-sha256") == plan_sha256, "Wrong plan label")
    env = config.get("Env", [])
    _require(type(env) is list and len(env) == len(approved_environment()) and set(env) == set(approved_environment()),
             "Unapproved container environment or numerical thread bound")
    for key, value in {"NetworkMode": "none", "ReadonlyRootfs": True, "Privileged": False,
                       "Memory": profile["memory_bytes"], "MemorySwap": profile["memory_bytes"],
                       "NanoCpus": profile["nano_cpus"], "PidsLimit": profile["pids_limit"],
                       "PidMode": "", "IpcMode": "private"}.items():
        _require(host.get(key) == value and (type(host.get(key)) is bool if type(value) is bool else True),
                 "Unexpected container setting: " + key)
    _require(host.get("CapDrop") == ["ALL"] and host.get("SecurityOpt") == ["no-new-privileges=true"],
             "Missing capability/privilege restrictions")
    for key in ("CapAdd", "Binds", "Devices", "DeviceRequests", "PortBindings", "VolumesFrom"):
        _require(not host.get(key), "Unapproved container capability: " + key)
    _require(host.get("LogConfig") == {"Type": "none", "Config": {}}, "Unapproved daemon log storage")
    _require(host.get("Tmpfs") == {"/tmp": profile["tmpfs"]}, "Unapproved temporary filesystem")
    mounts = record.get("Mounts", [])
    _require(len(mounts) == 2, "Unexpected container mounts")
    for destination, source, writable in (("/input", profile["input_dir"], False), ("/output", profile["output_dir"], True)):
        matching = [m for m in mounts if m.get("Destination") == destination]
        _require(len(matching) == 1 and matching[0].get("Type") == "bind"
                 and matching[0].get("Source") == source and matching[0].get("RW") is writable,
                 "Unapproved bind mount")


class LocalDocker:
    """Bind each command to one discovered local daemon, never an ambient override."""
    def __init__(self, *, endpoint=None):
        for name in ("DOCKER_HOST", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_CONTEXT"):
            _require(not os.environ.get(name), "Docker environment override is not admitted: " + name)
        located = shutil.which("docker")
        _require(located is not None, "Docker CLI is unavailable")
        self.executable = str(Path(located).resolve(strict=True))
        self.env = os.environ.copy()
        self.client_config = None
        self.endpoint = (local_endpoint(endpoint) if endpoint is not None else
                         local_endpoint(self._call(["context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"],
                                                  endpoint=False, json_result=True)))

    def command(self, arguments):
        config = ["--config", str(self.client_config)] if self.client_config is not None else []
        return [self.executable, *config, "--host", self.endpoint, *arguments]

    def isolate_client(self, directory):
        directory = Path(directory)
        directory.mkdir(mode=0o700, exist_ok=False)
        config = directory / "config.json"
        with config.open("xb") as handle:
            config.chmod(0o600)
            handle.write(canonical_json_bytes(CLIENT_CONFIG)); handle.flush(); os.fsync(handle.fileno())
        self.client_config = directory

    def _call(self, arguments, *, endpoint=True, json_result=False, timeout=30):
        command = self.command(arguments) if endpoint else [self.executable, *arguments]
        try:
            result = subprocess.run(command, env=self.env, stdin=subprocess.DEVNULL, capture_output=True,
                                    timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise NativeRuntimeError("Docker infrastructure call failed: " + type(error).__name__) from error
        _require(len(result.stdout) <= 4*1024*1024 and len(result.stderr) <= LOG_LIMIT, "Docker response exceeds bound")
        _require(result.returncode == 0, "Docker " + arguments[0] + " failed: " + result.stderr.decode("utf-8", "replace")[:3000])
        return json.loads(result.stdout) if json_result else result.stdout.decode("utf-8").strip()

    def inspect(self, container):
        values = self._call(["container", "inspect", container], json_result=True)
        _require(type(values) is list and len(values) == 1, "Ambiguous Docker container inspection")
        value = values[0]
        # Fail closed on injected values without preserving proxy credentials
        # from an unexpected daemon/client configuration in audit artifacts.
        value["Config"]["Env"] = _redact_unapproved_environment(value["Config"].get("Env", []))
        # The approved image and explicit env carry no credentials. Avoid copying
        # unrelated daemon/network data into the controlled record.
        return {key: value[key] for key in ("Id", "Name", "Image", "Config", "HostConfig", "Mounts", "State")}

    def identity(self):
        version = self._call(["version", "--format", "{{json .}}"], json_result=True)
        image = self._call(["image", "inspect", NATIVE_IMAGE_ID, "--format",
                            '{"id":{{json .Id}},"os":{{json .Os}},"architecture":{{json .Architecture}}}'], json_result=True)
        _require(image == {"id": NATIVE_IMAGE_ID, "os": "linux", "architecture": "amd64"},
                 "Approved native image is absent or mismatched; install it separately")
        keys = ("Version", "ApiVersion", "GitCommit", "Os", "Arch")
        daemon_id = self._call(["info", "--format", "{{.ID}}"])
        _require(type(daemon_id) is str and re.fullmatch(r"[A-Za-z0-9:._-]{1,256}", daemon_id), "Missing Docker daemon identity")
        return {"executable_sha256": hash_artifact(Path(self.executable))["sha256"], "daemon_id": daemon_id,
                "endpoint": self.endpoint, "client": {k: version["Client"].get(k) for k in keys},
                "server": {k: version["Server"].get(k) for k in keys}, "image": image}


def native_identity(runtime=None):
    """Pin the installed candidate and coordinator before approvals are issued."""
    runtime = LocalDocker() if runtime is None else runtime
    python = execution_identity()
    environment = {"coordinator": python["environment"], "docker": runtime.identity(),
                   "profile_version": PROFILE_VERSION, "uid": os.geteuid() or 65532,
                   "gid": os.getegid() if os.geteuid() else 65532}
    return {"implementation_sha256": python["implementation_sha256"],
            "environment_sha256": _digest(environment), "source": python["source"], "environment": environment,
            "driver_sha256": hash_artifact(DRIVER)["sha256"], "package_lock_sha256": hash_artifact(PACKAGE_LOCK)["sha256"],
            "runtime_image_id": NATIVE_IMAGE_ID, "platform": "linux/amd64",
            "qualification_status": "candidate_unqualified", "clinical_release_allowed": False}


def run_native(plan, inputs, outputs, logs, *, authorize_start, runtime=None, attempt_id=None, on_checkpoint=None,
               authority_guard=None, authorize_dispatch=None):
    """Run only the prescribed driver; retain failed transport and cleanup evidence.

    The caller must have checked signatures and preserved input/driver bytes.
    Memory is an enforced cgroup ceiling; a peak measurement is not fabricated.
    """
    runtime = LocalDocker() if runtime is None else runtime
    profile = container_profile(plan, inputs, outputs)
    _require(not list(Path(outputs).iterdir()), "Native output directory must be empty")
    _require(Path(logs).is_dir(), "Log directory must exist")
    _require(Path(inputs).parent == Path(outputs).parent == Path(logs).parent
             and len({Path(inputs), Path(outputs), Path(logs)}) == 3, "Native staging directories must be distinct siblings")
    runtime.isolate_client(Path(inputs).parent / "native-client")
    if os.geteuid() == 0:
        uid, gid = map(int, profile["user"].split(":"))
        # Only the caller's new staging directory is exposed to the container.
        # Its read-only bind prevents writes regardless of file ownership.
        Path(inputs).chmod(0o555)
        for path in Path(inputs).iterdir():
            _require(path.is_file() and not path.is_symlink(), "Nonregular staged native input")
            path.chmod(0o444)
        os.chown(outputs, uid, gid)
        Path(outputs).chmod(0o700)
    if attempt_id is not None:
        _require(type(attempt_id) is str and re.fullmatch(r"[0-9a-f]{32}", attempt_id), "Invalid operated attempt ID")
    name = "devharness-native-" + (attempt_id if attempt_id is not None else uuid.uuid4().hex)
    arguments = create_arguments(profile, plan.sha256(), name)
    record = {"schema_version": "native_transport/1", "profile": profile, "container_name": name,
              "command": runtime.command(arguments), "created": None, "finished": None,
              "cleanup": {"attempted": False, "removed": False}, "status": "failed",
              "memory_peak_mb": None, "memory_limit_verified": False,
              "memory_scope": "unknown; daemon cgroup limit not yet verified",
              "timed_out": False, "output_limit_exceeded": False}
    start, process, owned_id = time.monotonic(), None, None
    journal = (Path(logs)/"lifecycle.jsonl").open("xb")
    (Path(logs)/"lifecycle.jsonl").chmod(0o600)

    def checkpoint(event, payload):
        entry = {"schema_version": "native_lifecycle/1", "event": event,
            "recorded_at": datetime.now(timezone.utc).isoformat(), "plan_sha256": plan.sha256(),
            "container_name": name, "payload": payload}
        journal.write(canonical_json_bytes(entry) + b"\n")
        journal.flush(); os.fsync(journal.fileno())
        if on_checkpoint is not None:
            on_checkpoint(entry)

    def deadline():
        if time.monotonic()-start > plan.timeout_sec:
            record["timed_out"] = True
            raise NativeRuntimeError("Native execution watchdog deadline exceeded")

    def inspect_owned():
        owned = runtime.inspect(owned_id or name)
        _require(owned.get("Name") == "/"+name and owned.get("Image") == NATIVE_IMAGE_ID
                 and owned["Config"].get("Labels", {}).get("io.devharness.plan-sha256") == plan.sha256()
                 and re.fullmatch(r"[0-9a-f]{64}", owned.get("Id", ""))
                 and (owned_id is None or owned["Id"] == owned_id),
                 "Cannot establish ownership for container cleanup")
        return owned

    try:
        # This fsynced intent survives coordinator death after daemon creation.
        # Hard process/host failure still requires recovery; no automatic recovery
        # or completed audit receipt is claimed for such an interruption.
        checkpoint("intent", {"image_id": plan.runtime_image_id, "profile": profile, "command": record["command"]})
        deadline()
        cid = runtime._call(arguments)
        _require(re.fullmatch(r"[0-9a-f]{64}", cid), "Docker returned an invalid container ID")
        owned_id = cid
        checkpoint("created", {"container_id": cid})
        created = runtime.inspect(cid)
        record["created"] = created
        _require(created["Id"] == cid, "Created container ID mismatch")
        check_container_record(created, profile, plan.sha256(), name)
        record.update(memory_limit_verified=True, memory_scope="unknown; daemon cgroup ceiling configuration verified")
        deadline()
        # Image/create operations can take time. Recheck expiry immediately
        # before starting, and retain that authorization time for verification.
        with (Path(logs)/"stdout.log").open("xb") as stdout, (Path(logs)/"stderr.log").open("xb") as stderr:
            with (authority_guard() if authority_guard is not None else nullcontext(None)) as authority:
                if authority_guard is not None:
                    _require(type(authority) is dict,"Missing governed launch decision")
                    _require(callable(authorize_dispatch),"Governed dispatch requires an expiry check")
                record["launch_approvals"] = authorize_start()
                if authority_guard is not None:
                    authority=parse_json_bytes(canonical_json_bytes(authority))
                    record["launch_authority"]=authority
                authorization={"container_id": cid, "receipts": record["launch_approvals"]}
                if authority_guard is not None:authorization["authority"]=authority
                checkpoint("start_authorized", authorization)
                deadline()
                checkpoint("start_requested", {"container_id": cid})
                deadline()
                if authority_guard is not None:
                    # Durable writes and callbacks may outlive a grant. This
                    # check is after them, immediately before client dispatch.
                    authorize_dispatch(datetime.now(timezone.utc))
                process = subprocess.Popen(runtime.command(["start", "--attach", cid]), env=runtime.env,
                                           stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
                checkpoint("attach_started", {"container_id": cid, "client_pid": process.pid})
                if authority_guard is not None:
                    # Popen only proves that a Docker client exists. Hold the
                    # policy lock until the daemon has actually started the
                    # owned container, including a process that already exited.
                    while True:
                        deadline()
                        observed=runtime.inspect(cid)
                        _require(observed.get("Id")==cid,"Governed dispatch container ID changed")
                        check_container_record(observed,profile,plan.sha256(),name)
                        state=observed["State"];started_at=state.get("StartedAt")
                        if (type(started_at) is str and not started_at.startswith("0001-")
                                and state.get("Status") in {"running","exited"}):
                            stamp=datetime.fromisoformat(started_at.replace("Z","+00:00"))
                            _require(stamp.tzinfo is not None,"Native start observation lacks timezone")
                            _require(state.get("Running") is (state["Status"]=="running"),"Inconsistent native start state")
                            record["dispatch_observed"]=observed
                            checkpoint("authority_dispatch_observed",{"container":observed})
                            # Keep the observation even when delayed daemon
                            # startup crossed an approval/grant expiry boundary.
                            authorize_dispatch(stamp)
                            break
                        _require(process.poll() is None,"Native client finished without observed daemon start")
                        time.sleep(0.05)
            while process.poll() is None:
                deadline()
                try:
                    _require(stdout.tell() <= LOG_LIMIT and stderr.tell() <= LOG_LIMIT,
                             "Native output/log limit exceeded: stdout or stderr")
                    _check_live_outputs(outputs)
                except NativeRuntimeError:
                    record["output_limit_exceeded"] = True
                    raise
                time.sleep(0.05)
            record["client_exit_code"] = process.returncode
        deadline()
        record["finished"] = runtime.inspect(cid)
        check_container_record(record["finished"], profile, plan.sha256(), name)
        state = record["finished"]["State"]
        _require(state.get("Running") is False and state.get("Status") == "exited", "Native container did not reach an exited state")
        _require(process.returncode == 0 and state.get("ExitCode") == 0 and state.get("OOMKilled") is False,
                 "Native process failed: exit=" + str(state.get("ExitCode")) + ", oom=" + str(state.get("OOMKilled")))
        output_members = list(Path(outputs).iterdir())
        _require(len(output_members) == 1 and output_members[0].name == "result.json"
                 and not output_members[0].is_symlink() and output_members[0].is_file()
                 and output_members[0].stat().st_size <= RESULT_LIMIT, "Missing or invalid bounded native result")
        record["status"] = "ok"
    except Exception as error:
        record.update(error_type=type(error).__name__, error=str(error)[:6000])
    finally:
        # Client and daemon cleanup are independent. A stuck/failed client wait
        # must not prevent killing the owned daemon container or retaining state.
        try:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=5)
        except Exception as error:
            record["client_cleanup_error"] = type(error).__name__ + ": " + str(error)[:1000]
            record["status"] = "failed"
            record.setdefault("error_type", "NativeClientCleanupError")
            record.setdefault("error", "Native attach client cleanup could not be verified")
        try:
            owned = inspect_owned()
            owned_id = owned["Id"]
            record["cleanup"].update(attempted=True, container_id=owned_id)
            if owned["State"].get("Running") is True:
                try:
                    runtime._call(["kill", owned_id])
                except NativeRuntimeError as error:
                    # A process can exit between inspect and kill. Retain the
                    # error and require fresh proof of the same stopped object.
                    record["cleanup"]["kill_error"] = str(error)[:3000]
                owned = inspect_owned()
                _require(owned["State"].get("Running") is False
                         and owned["State"].get("Status") in {"exited", "dead"},
                         "Owned container is not observably stopped after kill")
            record["finished"] = owned
            checkpoint("finished", {"container_id": owned_id, "state": owned["State"]})
            runtime._call(["rm", owned_id])
            record["cleanup"]["removed"] = True
        except Exception as error:
            record["cleanup"]["error"] = str(error)[:3000]
            record["status"] = "failed"
            record.setdefault("error_type", "NativeCleanupError")
            record.setdefault("error", "Container cleanup could not be verified; inspect the retained UUID identity")
        try:
            checkpoint("cleanup", record["cleanup"])
        finally:
            journal.close()
        # Cap retained logs too. A truncation is explicit and cannot become a
        # successful transport even if the process exited between polls.
        for path in Path(logs).iterdir():
            if path.is_file() and path.stat().st_size > LOG_LIMIT:
                with path.open("r+b") as handle:
                    handle.truncate(LOG_LIMIT)
                record.update(status="failed", output_limit_exceeded=True,
                              error_type="NativeOutputLimit", error="Native log exceeded retention bound; truncated")
        record["elapsed_seconds"] = time.monotonic()-start
    return record
