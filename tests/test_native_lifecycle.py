"""Fault-injected supervisor tests, with no Docker or native statistical execution.

An empty JSON transport payload below is only an infrastructure double. These
tests do not validate a native analysis, model performance or clinical readiness.
"""
import copy
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from devharness_controlled import native_runtime as subject


class Limits:
    runtime_image_id = subject.NATIVE_IMAGE_ID
    platform = "linux/amd64"
    max_memory_mb = 768
    max_cpus = 1
    timeout_sec = 1

    def sha256(self):
        return "a" * 64


class Clock:
    """Advance virtual time without sleeping or launching a subprocess."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class DockerDouble:
    def __init__(self, plan, inputs, outputs, clock):
        self.plan = plan
        self.inputs = inputs
        self.outputs = outputs
        self.clock = clock
        self.profile = subject.container_profile(plan, inputs, outputs)
        self.env = {}
        self.client_config = None
        self.record = None
        self.calls = []
        self.on_create = lambda: None
        self.create_delay = 0
        self.create_error = None
        self.removed = False

    def isolate_client(self, directory):
        # Exercise the real local config writer; never initialize a Docker CLI.
        subject.LocalDocker.isolate_client(self, directory)

    def command(self, arguments):
        return ["docker-infrastructure-double", "--config", str(self.client_config), *arguments]

    def _call(self, arguments):
        self.calls.append(list(arguments))
        action = arguments[0]
        if action == "create":
            self.on_create()
            if self.create_error is not None:
                raise self.create_error
            self.clock.now += self.create_delay
            name = arguments[arguments.index("--name") + 1]
            profile = self.profile
            self.record = {
                "Id": "b" * 64,
                "Name": "/" + name,
                "Image": profile["image_id"],
                "Config": {
                    "User": profile["user"], "Entrypoint": ["/usr/bin/timeout"],
                    "Cmd": ["--signal=KILL", f"{self.plan.timeout_sec}s", "Rscript", *subject.COMMAND],
                    "Env": subject.approved_environment(),
                    "Labels": {"io.devharness.plan-sha256": self.plan.sha256()},
                },
                "HostConfig": {
                    "NetworkMode": "none", "ReadonlyRootfs": True, "Privileged": False,
                    "Memory": profile["memory_bytes"], "MemorySwap": profile["memory_bytes"],
                    "NanoCpus": profile["nano_cpus"], "PidsLimit": profile["pids_limit"],
                    "PidMode": "", "IpcMode": "private", "CapDrop": ["ALL"],
                    "SecurityOpt": ["no-new-privileges=true"], "CapAdd": None,
                    "Binds": None, "Devices": [], "DeviceRequests": None,
                    "PortBindings": {}, "VolumesFrom": None,
                    "LogConfig": {"Type": "none", "Config": {}},
                    "Tmpfs": {"/tmp": profile["tmpfs"]},
                },
                "Mounts": [
                    {"Type": "bind", "Source": str(self.inputs), "Destination": "/input", "RW": False},
                    {"Type": "bind", "Source": str(self.outputs), "Destination": "/output", "RW": True},
                ],
                "State": {"Running": False, "Status": "created", "ExitCode": 0, "OOMKilled": False},
            }
            return self.record["Id"]
        assert self.record is not None and arguments[1] == self.record["Id"]
        if action == "kill":
            self.record["State"].update(Running=False, Status="exited", ExitCode=137)
        elif action == "rm":
            assert self.record["State"]["Running"] is False
            self.removed = True
        else:
            raise AssertionError(f"Unexpected infrastructure-double call: {arguments}")
        return self.record["Id"]

    def inspect(self, container):
        self.calls.append(["inspect", container])
        if self.record is None:
            raise subject.NativeRuntimeError("No such synthetic container")
        assert container in (self.record["Id"], self.record["Name"].lstrip("/"))
        return copy.deepcopy(self.record)


class AttachDouble:
    pid = 43210

    def __init__(self, runtime, clock, mode):
        self.runtime = runtime
        self.clock = clock
        self.mode = mode
        self.returncode = None if mode in ("stuck_wait", "terminate_error") else 0
        self.calls = []
        if self.returncode is None:
            runtime.record["State"].update(Running=True, Status="running")
            clock.now += runtime.plan.timeout_sec + 1
        else:
            # This is a transport sentinel, not a native result or an oracle.
            (runtime.outputs / "result.json").write_text("{}")
            runtime.record["State"].update(Running=False, Status="exited", ExitCode=0)
            if mode == "exits_after_deadline":
                clock.now += runtime.plan.timeout_sec + 1

    def poll(self):
        return self.returncode

    def terminate(self):
        self.calls.append("terminate")
        if self.mode == "terminate_error":
            raise OSError("synthetic attach-client termination failure")

    def kill(self):
        self.calls.append("kill")

    def wait(self, timeout):
        self.calls.append(("wait", timeout))
        raise subprocess.TimeoutExpired("synthetic stuck Docker attach client", timeout)


@pytest.fixture
def harness(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    inputs, outputs, logs = (root / name for name in ("inputs", "outputs", "logs"))
    for directory in (inputs, outputs, logs):
        directory.mkdir()
    plan, clock = Limits(), Clock()
    runtime = DockerDouble(plan, inputs, outputs, clock)
    state = SimpleNamespace(plan=plan, clock=clock, runtime=runtime, inputs=inputs, outputs=outputs,
                            logs=logs, mode="immediate", attach=None, start_calls=[], authorizations=0,
                            authorization_delay=0)

    def authorize_start():
        state.authorizations += 1
        clock.now += state.authorization_delay
        return [{"scope": "infrastructure authorization double only"}]

    def start(arguments, **kwargs):
        state.start_calls.append(arguments)
        state.attach = AttachDouble(runtime, clock, state.mode)
        return state.attach

    # A module-local substitute ensures neither Popen nor any other subprocess
    # operation can accidentally reach a real Docker executable in these tests.
    monkeypatch.setattr(subject, "subprocess", SimpleNamespace(
        Popen=start, TimeoutExpired=subprocess.TimeoutExpired, DEVNULL=subprocess.DEVNULL))
    monkeypatch.setattr(subject, "time", clock)
    state.run = lambda **options: subject.run_native(plan, inputs, outputs, logs,
                                         authorize_start=authorize_start, runtime=runtime, **options)
    return state


def lifecycle(harness):
    return [json.loads(line) for line in (harness.logs / "lifecycle.jsonl").read_bytes().splitlines()]


def test_atomic_publication_temp_unlink_is_not_an_output_limit(harness, monkeypatch):
    """Reproduce a real writer unlinking its temp name after directory listing."""
    temporary = harness.outputs / ".native-nb-publication-race"
    original_iterdir = Path.iterdir
    observed = []

    def publication_listing(path):
        members = list(original_iterdir(path))
        if path == harness.outputs and temporary in members:
            assert (harness.outputs / "result.json").is_file()
            temporary.unlink()
            observed.append("unlinked_after_listing")
        return iter(members)

    def one_live_poll(attach):
        if not hasattr(attach, "polled_once"):
            attach.polled_once = True
            os.link(harness.outputs / "result.json", temporary)
            return None
        return attach.returncode

    monkeypatch.setattr(Path, "iterdir", publication_listing)
    monkeypatch.setattr(AttachDouble, "poll", one_live_poll)
    transport = harness.run()
    assert observed == ["unlinked_after_listing"]
    assert transport["status"] == "ok"
    assert transport["output_limit_exceeded"] is False
    assert transport["cleanup"]["removed"] is True


@pytest.mark.parametrize("after_kill", ["exited", "still_running", "different_id"])
def test_kill_exit_race_requires_observed_stopped_owned_container(harness, after_kill):
    # The original timeout remains a failure even when cleanup is recovered.
    harness.mode = "terminate_error"
    call = harness.runtime._call
    inspect = harness.runtime.inspect

    def inspect_response(container):
        if harness.runtime.record and harness.runtime.record["Id"] == "c" * 64:
            harness.runtime.calls.append(["inspect", container])
            return copy.deepcopy(harness.runtime.record)
        return inspect(container)

    def kill_race(arguments):
        if arguments[0] == "kill":
            harness.runtime.calls.append(list(arguments))
            if after_kill != "still_running":
                harness.runtime.record["State"].update(Running=False, Status="exited", ExitCode=0)
            if after_kill == "different_id":
                harness.runtime.record["Id"] = "c" * 64
            raise subject.NativeRuntimeError("Synthetic daemon kill response: container is not running")
        return call(arguments)

    harness.runtime._call = kill_race
    harness.runtime.inspect = inspect_response
    transport = harness.run()
    assert transport["status"] == "failed" and transport["timed_out"] is True
    assert transport["cleanup"]["removed"] is (after_kill == "exited")
    if after_kill == "exited":
        assert transport["finished"]["State"]["Status"] == "exited"
        assert "not running" in transport["cleanup"]["kill_error"]
    else:
        assert not any(call[0] == "rm" for call in harness.runtime.calls)


def test_failed_durable_start_intent_prevents_actual_start(harness):
    observed = []
    def persist(entry):
        observed.append(entry["event"])
        if entry["event"] == "start_requested":
            assert not harness.start_calls
            raise OSError("synthetic registry write failure before dispatch")
    transport = harness.run(on_checkpoint=persist)
    assert transport["status"] == "failed" and not harness.start_calls
    assert "start_requested" in observed and "attach_started" not in observed
    assert transport["cleanup"]["removed"] is True


@pytest.mark.parametrize("client_failure", ["stuck_wait", "terminate_error"])
def test_client_cleanup_failure_cannot_skip_owned_daemon_cleanup(harness, client_failure):
    harness.mode = client_failure
    transport = harness.run()

    assert transport["status"] == "failed" and transport["timed_out"] is True
    assert "client_cleanup_error" in transport
    assert transport["cleanup"]["attempted"] is True
    assert transport["cleanup"]["removed"] is True
    assert harness.runtime.removed is True
    actions = [call[0] for call in harness.runtime.calls]
    assert actions.count("kill") == 1 and actions.count("rm") == 1
    assert actions.index("kill") < actions.index("rm")
    assert transport["finished"]["State"]["Running"] is False
    events = lifecycle(harness)
    assert events[-1]["event"] == "cleanup" and events[-1]["payload"]["removed"] is True
    if client_failure == "stuck_wait":
        assert harness.attach.calls == ["terminate", ("wait", 5), "kill", ("wait", 5)]
        assert "TimeoutExpired" in transport["client_cleanup_error"]


def test_delayed_create_cannot_start_after_the_signed_deadline(harness):
    harness.runtime.create_delay = 20
    transport = harness.run()

    assert transport["status"] == "failed" and transport["timed_out"] is True
    assert transport["elapsed_seconds"] >= 20 > harness.plan.timeout_sec
    assert harness.start_calls == [] and harness.authorizations == 0
    assert transport["created"] is not None
    assert transport["cleanup"]["removed"] is True
    assert not any(call[0] == "kill" for call in harness.runtime.calls)
    assert [event["event"] for event in lifecycle(harness)] == ["intent", "created", "finished", "cleanup"]


def test_client_exit_between_polls_cannot_hide_an_expired_deadline(harness):
    harness.mode = "exits_after_deadline"
    transport = harness.run()

    assert len(harness.start_calls) == 1
    assert transport["client_exit_code"] == 0
    assert transport["status"] == "failed" and transport["timed_out"] is True
    assert transport["cleanup"]["removed"] is True


def test_authorization_work_cannot_start_after_the_signed_deadline(harness):
    harness.authorization_delay = 2
    transport = harness.run()

    assert transport["status"] == "failed" and transport["timed_out"] is True
    assert harness.authorizations == 1
    assert harness.start_calls == []
    assert transport["cleanup"]["removed"] is True


def test_lifecycle_intent_is_written_and_fsynced_before_daemon_create(harness, monkeypatch):
    journal = harness.logs / "lifecycle.jsonl"
    real_fsync = os.fsync
    synchronized_bytes = []
    checked_before_create = []

    def observe_fsync(fd):
        real_fsync(fd)
        if journal.exists() and os.fstat(fd).st_ino == journal.stat().st_ino:
            synchronized_bytes.append(journal.read_bytes())

    monkeypatch.setattr(subject.os, "fsync", observe_fsync)

    def before_create():
        raw = journal.read_bytes()
        assert synchronized_bytes and synchronized_bytes[-1] == raw
        assert raw.endswith(b"\n")
        events = [json.loads(line) for line in raw.splitlines()]
        assert len(events) == 1 and events[0]["event"] == "intent"
        intent = events[0]
        assert intent["plan_sha256"] == harness.plan.sha256()
        assert intent["payload"]["image_id"] == harness.plan.runtime_image_id
        assert intent["payload"]["profile"] == harness.runtime.profile
        assert intent["container_name"].startswith("devharness-native-")
        assert intent["container_name"] in intent["payload"]["command"]
        assert journal.stat().st_mode & 0o777 == 0o600
        checked_before_create.append(intent)

    harness.runtime.on_create = before_create
    transport = harness.run()

    assert len(checked_before_create) == 1
    assert transport["status"] == "ok"  # Supervisor transport only; no analysis ran.
    assert transport["container_name"] == checked_before_create[0]["container_name"]
    assert lifecycle(harness)[-1]["event"] == "cleanup"


def test_failed_create_does_not_claim_a_verified_memory_limit(harness):
    harness.runtime.create_error = subject.NativeRuntimeError("synthetic create failure")
    transport = harness.run()

    assert transport["status"] == "failed" and transport["created"] is None
    assert transport["memory_limit_verified"] is False
    assert "not yet verified" in transport["memory_scope"]
    assert harness.start_calls == []
    assert lifecycle(harness)[0]["event"] == "intent"
