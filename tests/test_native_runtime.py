"""Isolation/transport boundary tests; these do not measure native model performance."""

import copy
from pathlib import Path

import pytest

from devharness_controlled.native_runtime import (
    NATIVE_IMAGE_ID,
    NativeRuntimeError,
    _redact_unapproved_environment,
    approved_environment,
    check_container_record,
    container_profile,
    create_arguments,
    local_endpoint,
)


class Limits:
    runtime_image_id = NATIVE_IMAGE_ID
    platform = "linux/amd64"
    max_memory_mb = 768
    max_cpus = 1
    timeout_sec = 60

    def sha256(self):
        return "a" * 64


def evidence(tmp_path):
    inputs, outputs = tmp_path / "input", tmp_path / "output"
    inputs.mkdir()
    outputs.mkdir()
    profile = container_profile(Limits(), inputs, outputs, uid=1000, gid=1000)
    record = {
        "Id": "b" * 64,
        "Name": "/devharness-native-abc",
        "Image": NATIVE_IMAGE_ID,
        "Config": {
            "User": "1000:1000",
            "Entrypoint": ["/usr/bin/timeout"],
            "Cmd": [
                "--signal=KILL",
                "60s",
                "Rscript",
                "--vanilla",
                "/input/native_nb.R",
                "--config",
                "/input/request.json",
                "--output",
                "/output/result.json",
            ],
            "Labels": {"io.devharness.plan-sha256": "a" * 64},
            "Env": approved_environment(),
        },
        "HostConfig": {
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "Privileged": False,
            "CapDrop": ["ALL"],
            "CapAdd": None,
            "SecurityOpt": ["no-new-privileges=true"],
            "Memory": 768 * 1024**2,
            "MemorySwap": 768 * 1024**2,
            "NanoCpus": 10**9,
            "PidsLimit": 64,
            "PidMode": "",
            "IpcMode": "private",
            "Binds": None,
            "Devices": [],
            "DeviceRequests": None,
            "PortBindings": {},
            "VolumesFrom": None,
            "LogConfig": {"Type": "none", "Config": {}},
            "Tmpfs": {"/tmp": profile["tmpfs"]},
        },
        "Mounts": [
            {
                "Type": "bind",
                "Source": str(inputs),
                "Destination": "/input",
                "RW": False,
            },
            {
                "Type": "bind",
                "Source": str(outputs),
                "Destination": "/output",
                "RW": True,
            },
        ],
        "State": {
            "Running": False,
            "OOMKilled": False,
            "ExitCode": 0,
            "Status": "exited",
        },
    }
    return profile, record


def test_fixed_profile_accepts_only_the_prescribed_runtime(tmp_path):
    profile, record = evidence(tmp_path)
    check_container_record(record, profile, "a" * 64, "devharness-native-abc")
    args = create_arguments(profile, "a" * 64, "devharness-native-abc")
    assert args[0] == "create" and "--pull=never" in args
    assert NATIVE_IMAGE_ID in args and "--privileged" not in args
    assert args[-9:] == record["Config"]["Cmd"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("NetworkMode", "host"),
        ("ReadonlyRootfs", False),
        ("Privileged", True),
        ("Memory", 0),
        ("MemorySwap", -1),
        ("NanoCpus", 0),
        ("PidsLimit", 0),
        ("CapAdd", ["SYS_ADMIN"]),
        ("PidMode", "host"),
        ("IpcMode", "host"),
        ("SecurityOpt", ["seccomp=unconfined"]),
        ("Binds", ["/:/host"]),
        ("Devices", [{"PathOnHost": "/dev/sda"}]),
        ("VolumesFrom", ["other"]),
        ("LogConfig", {"Type": "json-file"}),
        ("Tmpfs", {"/tmp": "rw,exec"}),
    ],
)
def test_changed_isolation_is_rejected(tmp_path, field, value):
    profile, record = evidence(tmp_path)
    record["HostConfig"][field] = value
    with pytest.raises(NativeRuntimeError):
        check_container_record(record, profile, "a" * 64, "devharness-native-abc")


@pytest.mark.parametrize(
    "change",
    ["image", "root", "command", "input_write", "extra_mount", "label", "threads"],
)
def test_identity_or_command_substitution_is_rejected(tmp_path, change):
    profile, record = evidence(tmp_path)
    if change == "image":
        record["Image"] = "sha256:" + "0" * 64
    if change == "root":
        record["Config"]["User"] = "0:0"
    if change == "command":
        record["Config"]["Cmd"] = ["arbitrary.R"]
    if change == "input_write":
        record["Mounts"][0]["RW"] = True
    if change == "extra_mount":
        record["Mounts"].append(
            {"Type": "bind", "Source": "/", "Destination": "/host", "RW": True}
        )
    if change == "label":
        record["Config"]["Labels"] = {}
    if change == "threads":
        record["Config"]["Env"][0] += "0"
    with pytest.raises(NativeRuntimeError):
        check_container_record(record, profile, "a" * 64, "devharness-native-abc")


@pytest.mark.parametrize(
    "endpoint",
    ["tcp://127.0.0.1:2375", "ssh://remote", "unix://relative", "", "unix:///tmp/a\n"],
)
def test_only_explicit_local_unix_daemon_is_admitted(endpoint):
    with pytest.raises(NativeRuntimeError):
        local_endpoint(endpoint)


def test_mount_option_injection_and_mutable_image_are_rejected(tmp_path):
    bad = tmp_path / "a,readonly=false"
    bad.mkdir()
    with pytest.raises(NativeRuntimeError):
        container_profile(Limits(), bad, tmp_path, uid=1000, gid=1000)
    limits = copy.copy(Limits())
    limits.runtime_image_id = "devharness/native-nb:latest"
    with pytest.raises(NativeRuntimeError):
        container_profile(limits, tmp_path, tmp_path, uid=1000, gid=1000)


def test_unapproved_environment_is_rejected_and_redacted_before_retention(tmp_path):
    profile, record = evidence(tmp_path)
    record["Config"]["Env"].append(
        "HTTP_PROXY=https://fake-user:fake-secret@example.invalid"
    )
    with pytest.raises(NativeRuntimeError, match="environment"):
        check_container_record(record, profile, "a" * 64, "devharness-native-abc")
    redacted = _redact_unapproved_environment(record["Config"]["Env"])
    assert not any("fake-secret" in entry for entry in redacted)
    assert "HTTP_PROXY=<unapproved-redacted>" in redacted


def test_historical_profile_one_keeps_its_original_command(tmp_path):
    profile, record = evidence(tmp_path)
    profile["schema_version"] = "native_container_profile/1"
    record["Config"]["Entrypoint"] = ["Rscript"]
    record["Config"]["Cmd"] = record["Config"]["Cmd"][3:]
    check_container_record(record, profile, "a" * 64, "devharness-native-abc")
    args = create_arguments(profile, "a" * 64, "devharness-native-abc")
    assert args[-6:] == record["Config"]["Cmd"] and "/usr/bin/timeout" not in args
    profile["schema_version"] = "native_container_profile/2"
    with pytest.raises(NativeRuntimeError, match="command"):
        check_container_record(record, profile, "a" * 64, "devharness-native-abc")


@pytest.mark.parametrize("duration", [0, -1, True, "60", 60.5, 86401])
def test_container_deadline_cannot_be_disabled_or_injected(tmp_path, duration):
    profile, _ = evidence(tmp_path)
    profile["timeout_sec"] = duration
    with pytest.raises(NativeRuntimeError, match="deadline"):
        create_arguments(profile, "a" * 64, "devharness-native-abc")


@pytest.mark.parametrize("kind", ["symlink", "directory", "oversized", "three_names"])
def test_live_publication_observer_retains_output_safety_bounds(tmp_path, kind):
    from devharness_controlled.native_runtime import RESULT_LIMIT, _check_live_outputs

    path = tmp_path / "result.json"
    if kind == "symlink":
        path.symlink_to(tmp_path / "missing-target")
    elif kind == "directory":
        path.mkdir()
    elif kind == "oversized":
        with path.open("wb") as output:
            output.truncate(RESULT_LIMIT + 1)
    else:
        for i in range(3):
            (tmp_path / f".native-nb-{i}").write_text("{}")
    with pytest.raises(NativeRuntimeError, match="limit exceeded"):
        _check_live_outputs(tmp_path)


def test_only_the_expected_temporary_name_may_disappear_during_live_inspection(
    tmp_path, monkeypatch
):
    from devharness_controlled.native_runtime import _check_live_outputs

    path = tmp_path / "result.json"
    path.write_text("{}")
    original = Path.lstat

    def concurrent_unlink(p):
        if p == path:
            path.unlink()
        return original(p)

    monkeypatch.setattr(Path, "lstat", concurrent_unlink)
    with pytest.raises(NativeRuntimeError, match="disappeared"):
        _check_live_outputs(tmp_path)


@pytest.mark.parametrize(
    "platform,image_id",
    [
        (
            "linux/arm64",
            "sha256:4f31355f49e3fb7282db42a750a195b4aea186adeb10e685bfe863723817f2df",
        ),
        (
            "linux/amd64",
            "sha256:cb5e0f5371c836e0adb3a2ae53c98526089856a817a66d7a86fbe82add2b5cab",
        ),
    ],
)
def test_pinned_kernel_profile_and_historical_contract(tmp_path, platform, image_id):
    historical, record = evidence(tmp_path)
    limits = Limits()
    limits.runtime_image_id = image_id
    limits.platform = platform
    profile = container_profile(
        limits, tmp_path / "input", tmp_path / "output", uid=1000, gid=1000
    )
    assert profile["schema_version"] == "native_container_profile/3"
    assert profile["platform"] == platform
    args = create_arguments(profile, limits.sha256(), "devharness-native-abc")
    assert args[args.index("--platform") + 1] == platform
    assert image_id in args and "DEVBENCH_NATIVE_EDGE_R=4.10.7" in args
    new_record = copy.deepcopy(record)
    new_record["Image"] = image_id
    new_record["Config"]["Env"] = approved_environment(image_id=image_id)
    check_container_record(
        new_record, profile, limits.sha256(), "devharness-native-abc"
    )
    with pytest.raises(NativeRuntimeError, match="profile"):
        check_container_record(
            new_record,
            {**profile, "schema_version": "native_container_profile/2"},
            limits.sha256(),
            "devharness-native-abc",
        )
    check_container_record(record, historical, limits.sha256(), "devharness-native-abc")
