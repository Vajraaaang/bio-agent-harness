"""Synthetic recovery tests for the mounted OCI launch fence."""

import os
from uuid import uuid4

import pytest

from devbench.engine import mounted_kernel_oci as oci

ATTEMPT = "a" * 32
REQUEST = "b" * 64
IMAGE = "sha256:" + "c" * 64
CONTAINER = "d" * 64


class Docker:
    endpoint = "unix:///synthetic/docker.sock"

    def __init__(self, stage):
        self.stage = stage
        self.containers = {}
        self.removed = []
        self.calls = []
        self.daemon = "synthetic-daemon"
        self.interrupt_after_create = False
        self.interrupt_after_start = False

    def isolate_client(self, directory):
        directory.mkdir()

    def _call(self, args, **options):
        self.calls.append(args)
        if args[0] == "info":
            return self.daemon
        if args[:2] == ["image", "inspect"]:
            return {
                "id": IMAGE,
                "os": "linux",
                "arch": "arm64",
                "labels": {
                    "org.opencontainers.image.title": "devbench-rna-kernel",
                    "org.opencontainers.image.version": "2.0",
                },
            }
        if args[0] == "create":
            name = args[args.index("--name") + 1]
            request = args[args.index("--label") + 1].split("=", 1)[1]
            self.containers[CONTAINER] = {
                "Name": "/" + name,
                "Image": IMAGE,
                "Id": CONTAINER,
                "Config": {"Labels": {oci._LABEL: request}},
                "HostConfig": {
                    "NetworkMode": "none",
                    "ReadonlyRootfs": True,
                    "Privileged": False,
                },
                "Mounts": [
                    {
                        "Type": "bind",
                        "Source": str(self.stage / source),
                        "Destination": "/" + source,
                        "RW": source == "outputs",
                    }
                    for source in ("inputs", "outputs")
                ],
                "State": {"ExitCode": 0, "Running": False},
            }
            if self.interrupt_after_create:
                raise KeyboardInterrupt
            return CONTAINER
        if args[:2] == ["container", "ls"]:
            return self.list_selected(args[-1])
        if args[0] == "rm":
            self.removed.append(args[-1])
            del self.containers[args[-1]]
            return args[-1]
        if args[0] == "start":
            if self.interrupt_after_start:
                self.containers[args[-1]]["State"]["Running"] = True
                raise KeyboardInterrupt
            return ""
        raise AssertionError(args)

    def list_selected(self, selector):
        if selector.startswith("name="):
            name = selector.removeprefix("name=^").removesuffix("$")
            return "\n".join(
                key for key, value in self.containers.items() if value["Name"] == name
            )
        container = selector.removeprefix("id=")
        return container if container in self.containers else ""

    def inspect(self, container):
        return self.containers[container]


@pytest.fixture
def staging(workspace_tmp_path, monkeypatch):
    stage = workspace_tmp_path / ".work"
    stage.mkdir()
    (stage / "inputs").mkdir()
    (stage / "outputs").mkdir()
    runtime = Docker(stage)
    monkeypatch.setattr(oci, "LocalDocker", lambda **opts: runtime)
    return stage, runtime


def recover(stage):
    return oci.recover_mounted_container(
        stage,
        request_sha=REQUEST,
        image_id=IMAGE,
        attempt_id=ATTEMPT,
    )


def launch(stage):
    return oci.run_mounted_container(
        stage,
        request_sha=REQUEST,
        image_id=IMAGE,
        attempt_id=ATTEMPT,
        timeout=10,
        memory_mb=512,
    )


def test_recovery_fences_an_authorized_worker_before_any_create(staging):
    stage, docker = staging
    assert recover(stage)["disposition"] == "not_created"
    with pytest.raises(oci.NativeRuntimeError, match="disarmed"):
        launch(stage)
    assert docker.calls == []


def test_interruption_after_daemon_create_recovers_exact_container(staging):
    stage, docker = staging
    docker.interrupt_after_create = True
    with pytest.raises(KeyboardInterrupt):
        launch(stage)
    assert docker.removed == []
    with pytest.raises(oci.NativeRuntimeError, match="ownership"):
        docker.containers[CONTAINER]["Config"]["Labels"][oci._LABEL] = "wrong"
        recover(stage)
    assert docker.removed == []
    docker.containers[CONTAINER]["Config"]["Labels"][oci._LABEL] = REQUEST
    proof = recover(stage)
    assert proof["container_id"] == CONTAINER and proof["disposition"] == "removed"
    assert recover(stage) == proof and docker.removed == [CONTAINER]


def test_active_worker_lock_prevents_recovery(staging):
    stage, docker = staging
    with oci._oci_lock(stage.parent):
        with pytest.raises(oci.NativeRuntimeError, match="active"):
            recover(stage)
    assert docker.calls == []


def test_recovery_rejects_renamed_container_and_daemon_change(staging):
    stage, docker = staging
    docker.interrupt_after_start = True
    with pytest.raises(KeyboardInterrupt):
        launch(stage)
    owned = docker.containers[CONTAINER]
    original_name = owned["Name"]
    owned["Name"] = "/renamed"
    with pytest.raises(oci.NativeRuntimeError, match="container name changed"):
        recover(stage)
    owned["Name"] = original_name
    docker.daemon = "new-daemon"
    with pytest.raises(oci.NativeRuntimeError, match="daemon mismatch"):
        recover(stage)
    docker.daemon = "synthetic-daemon"
    assert recover(stage)["disposition"] == "removed"
    assert docker.removed == [CONTAINER]


def test_recovery_reuses_cleanup_completed_before_receipt_publication(staging):
    stage, docker = staging
    normal = launch(stage)
    proof = recover(stage)
    assert proof == {
        key: value
        for key, value in normal.items()
        if key not in {"platform", "error_type"}
    }
    assert docker.removed == [CONTAINER]


def test_create_without_a_daemon_response_keeps_uncertain_state(staging):
    stage, docker = staging
    docker.interrupt_after_create = True
    with pytest.raises(KeyboardInterrupt):
        launch(stage)
    docker.containers.clear()
    with pytest.raises(oci.NativeRuntimeError, match="create is uncertain"):
        recover(stage)
    assert docker.removed == []


def test_workflow_recovery_records_confirmed_cleanup_for_interrupted_oci(
    workspace_tmp_path, monkeypatch
):
    from devbench.engine import tier_a_executor
    from devbench.engine.workflow_operations import OperationBlocked
    from devbench.platform.workbench_adaptive import _reconcile_cancelled_cleanup
    from tests.test_tier_a_executor import _fixture, _submit

    flow, executor, envelope = _fixture(
        workspace_tmp_path, input_format="h5", mounted_image_id=IMAGE
    )
    docker = Docker(None)
    docker.interrupt_after_create = True
    monkeypatch.setattr(oci, "LocalDocker", lambda **opts: docker)

    def interrupted(worker, args, *, diagnostics, _before_start):
        _before_start()
        docker.stage = args[0]
        worker(*args)

    monkeypatch.setattr(tier_a_executor, "run_sandboxed", interrupted)
    with pytest.raises(KeyboardInterrupt):
        _submit(flow, executor, envelope)
    dispatches = flow._read_projection(lambda db: flow._state(db).dispatches)
    (entry,) = dispatches.values()
    token = entry["token"]
    original = docker.stage.rename(docker.stage.with_name(".original-stage"))
    docker.stage.mkdir()
    with pytest.raises(OperationBlocked, match="custody"):
        executor.recover(flow, token)
    assert docker.removed == []
    docker.stage.rmdir()
    original.rename(docker.stage)
    revision = flow._read_projection(
        lambda db: flow.authority._revision(db, flow.lineage_id)
    )
    flow.authority.cancel_execution(
        flow.lineage_id, actor="fixture", expected_revision=revision
    )
    assert _reconcile_cancelled_cleanup(flow, executor)["cleanup_status"] == "confirmed"
    assert flow.authority.attempt(token)["cleanup_state"] == "confirmed"
    assert docker.removed == [CONTAINER]
    assert not docker.stage.exists()
    assert executor.recover(flow, token)["reason"] == tier_a_executor.TIER_A_INTERRUPTED


def test_real_oci_create_without_reply_is_removed(workspace_tmp_path, monkeypatch):
    image = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE")
    if not image:
        pytest.skip("Requires a locally pinned mounted kernel image")
    original = oci.LocalDocker

    class InterruptedDocker(original):
        def _call(self, arguments, **options):
            result = super()._call(arguments, **options)
            if arguments[0] == "create":
                raise KeyboardInterrupt
            return result

    monkeypatch.setattr(oci, "LocalDocker", InterruptedDocker)
    stage = workspace_tmp_path / ".work"
    stage.mkdir()
    (stage / "inputs").mkdir()
    (stage / "outputs").mkdir()
    binding = {"request_sha": REQUEST, "image_id": image, "attempt_id": uuid4().hex}
    with pytest.raises(KeyboardInterrupt):
        oci.run_mounted_container(stage, **binding, timeout=10, memory_mb=512)
    proof = oci.recover_mounted_container(stage, **binding)
    assert proof["disposition"] == "removed" and proof["cleanup_confirmed"]
    assert oci.recover_mounted_container(stage, **binding) == proof
