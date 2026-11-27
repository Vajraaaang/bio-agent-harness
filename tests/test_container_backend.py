"""Synthetic ADR-20 contracts and opt-in live execution of pinned workers."""

from dataclasses import replace
from hashlib import sha256
from uuid import uuid4

import pytest

from devbench.engine import container_backend as backends
from devbench.engine import mounted_kernel_oci as mounted
from devbench.engine.container_backend import (
    ContainerBackend,
    DockerBackend,
    NonOciPublicFallbackBackend,
    PodmanBackend,
    ResourceProfile,
)
from devbench.engine.mounted_worker_registry import (
    BUILTIN_WORKERS,
    MountedWorker,
    MountedWorkerRegistry,
)
from devharness_controlled.native_runtime import NativeRuntimeError
from tests.test_mounted_oci_recovery import ATTEMPT, IMAGE, REQUEST, Docker


def _stage(root):
    stage = root / "stage"
    stage.mkdir(mode=0o700)
    for name in ("inputs", "outputs"):
        (stage / name).mkdir(mode=0o700)
    return stage


def _pins():
    return dict(attempt_id=ATTEMPT, image_id=IMAGE, request_sha=REQUEST)


def test_inner_r_watchdog_uses_verified_worker_deadline(
    workspace_tmp_path, monkeypatch
):
    from rna_kernels.mounted_model import _r_solver_timeout

    key = "DEVBENCH_MOUNTED_TIMEOUT_SEC"
    monkeypatch.delenv(key, raising=False)
    assert _r_solver_timeout() == 240
    stage = _stage(workspace_tmp_path)
    argv = mounted._mount_arguments(
        stage, IMAGE, "fixture", REQUEST, 450, 4096, "linux/arm64"
    )
    command = argv[argv.index(IMAGE) + 1 :]
    assert command[:4] == ["--signal=KILL", "450", "/usr/bin/env", key + "=450"]
    monkeypatch.setenv(key, command[3].split("=", 1)[1])
    assert _r_solver_timeout() == 450
    monkeypatch.setenv(key, "1")
    assert _r_solver_timeout() == 1
    for invalid in ("0", "-1", "inf", "1.5", "259201"):
        monkeypatch.setenv(key, invalid)
        with pytest.raises(ValueError, match="deadline"):
            _r_solver_timeout()


def test_registry_admits_frozen_module_and_profile_limits(workspace_tmp_path):
    stage = _stage(workspace_tmp_path)
    runtime = Docker(stage)
    ceiling = ResourceProfile(6, 8192, 320, 256, 2400, gpu=True)
    worker = MountedWorker("curated.example", ceiling, image_ids=frozenset({IMAGE}))
    backend = DockerBackend(
        registry=MountedWorkerRegistry((worker,)), runtime_factory=lambda **kw: runtime
    )
    assert isinstance(backend, ContainerBackend)
    for profile in (
        replace(ceiling, cpus=6.5),
        replace(ceiling, memory_mb=8193),
        replace(ceiling, pids_limit=321),
        replace(ceiling, scratch_mb=257),
        replace(ceiling, timeout_sec=2401),
    ):
        with pytest.raises(ValueError, match="ceiling"):
            mounted.run_mounted_container(
                stage,
                backend=backend,
                worker_module=worker.module,
                resource_profile=profile,
                **_pins(),
            )
    with pytest.raises(ValueError, match="registered"):
        mounted.run_mounted_container(
            stage, backend=backend, worker_module="unregistered.module", **_pins()
        )
    with pytest.raises(ValueError, match="image"):
        mounted.run_mounted_container(
            stage,
            backend=backend,
            worker_module=worker.module,
            **{**_pins(), "image_id": "sha256:" + "9" * 64},
        )
    assert not runtime.calls
    receipt = mounted.run_mounted_container(
        stage,
        backend=backend,
        worker_module=worker.module,
        resource_profile=ceiling,
        **_pins(),
    )
    create = next(args for args in runtime.calls if args[0] == "create")
    for flag, value in (
        ("--cpus", "6"),
        ("--memory", "8192m"),
        ("--memory-swap", "8192m"),
        ("--pids-limit", "320"),
        ("--gpus", "all"),
        ("--network", "none"),
        ("--ipc", "private"),
        ("--security-opt", "no-new-privileges:true"),
        ("--cap-drop", "ALL"),
    ):
        assert create[create.index(flag) + 1] == value
    assert "--pull=never" in create and "--read-only" in create
    assert create[-4:] == ["python3.13", "-m", worker.module, REQUEST]
    assert create[create.index("--tmpfs") + 1].endswith("size=268435456,mode=1777")
    assert receipt["isolation_backend"] == "docker" and receipt["oci_isolated"] is True
    assert receipt["clinical_release_allowed"] is False
    assert mounted.recover_mounted_container(stage, backend=backend, **_pins()) == {
        k: v for k, v in receipt.items() if k not in {"platform", "error_type"}
    }


class _RootlessPodman(Docker):
    def __init__(self, stage, store):
        super().__init__(stage)
        self.store = store
        self.host = {
            "os": "linux",
            "security": {"rootless": True},
            "serviceIsRemote": False,
            "cgroupManager": "systemd",
            "cgroupVersion": "v2",
            "cgroupControllers": ["cpu", "memory", "pids"],
        }

    def _call(self, args, **kw):
        if args[0] == "info":
            return {
                "host": self.host,
                "store": {"graphRoot": str(self.store), "graphDriverName": "overlay"},
            }
        if args[:2] == ["image", "ls"]:
            return IMAGE
        if args[:2] == ["image", "inspect"]:
            facts = super()._call(args, **kw)
            return [
                {
                    "Id": facts["id"][7:],
                    "Os": "linux",
                    "Architecture": "arm64",
                    "Labels": facts["labels"],
                }
            ]
        return super()._call(args, **kw)


def test_podman_revalidates_delegation_before_create_and_store_before_cleanup(
    workspace_tmp_path, monkeypatch
):
    stage = _stage(workspace_tmp_path)
    store = workspace_tmp_path / "store"
    store.mkdir(mode=0o700)
    runtime = _RootlessPodman(stage, store)
    backend = PodmanBackend(runtime_factory=lambda authfile: runtime)
    assert isinstance(backend, ContainerBackend)
    monkeypatch.setattr(backends, "_delegated", lambda host: False)
    assert backend.probe() == {
        "isolation_backend": "podman",
        "oci_isolated": False,
        "available": True,
        "local_image_ids": [],  # Unregistered synthetic store entry.
        "cgroups_v2_delegated": False,
        "clinical_release_allowed": False,
    }
    for classification, tier in (
        ("private", "none"),
        ("clinical", "none"),
        ("public", "L2_statistically_calibrated"),
    ):
        with pytest.raises(NativeRuntimeError, match="delegation"):
            mounted.run_mounted_container(
                stage,
                backend=backend,
                data_classification=classification,
                claim_tier=tier,
                **_pins(),
            )
    assert not any(call[0] == "create" for call in runtime.calls)
    monkeypatch.setattr(backends, "_delegated", lambda host: True)
    assert backend.probe()["oci_isolated"] is True
    runtime.interrupt_after_start = True
    with pytest.raises(KeyboardInterrupt):
        mounted.run_mounted_container(stage, backend=backend, **_pins())
    create = next(args for args in runtime.calls if args[0] == "create")
    assert "--userns=keep-id" in create
    assert "--http-proxy=false" in create
    assert "bind-nonrecursive" in create[create.index("--mount") + 1]
    alternative = workspace_tmp_path / "other"
    alternative.mkdir(mode=0o700)
    runtime.store = alternative
    with pytest.raises(NativeRuntimeError, match="storage mismatch"):
        mounted.recover_mounted_container(stage, backend=backend, **_pins())
    assert not runtime.removed
    runtime.store = store
    proof = mounted.recover_mounted_container(stage, backend=backend, **_pins())
    assert (
        proof["storage_id"] and proof["cleanup_confirmed"] and "daemon_id" not in proof
    )


def test_advertised_controllers_without_delegation_are_insufficient(
    workspace_tmp_path, monkeypatch
):
    root = workspace_tmp_path / "cgroup"
    root.mkdir()
    host = {"cgroupVersion": "v2", "cgroupControllers": ["cpu", "memory", "pids"]}
    monkeypatch.setattr(backends, "_delegation_root", lambda host: root)
    control = root / "cgroup.subtree_control"
    control.write_text("cpu memory")
    assert not backends._delegated(host)
    control.write_text("cpu memory pids")
    # An ordinary directory cannot create child controller files.
    assert not backends._delegated(host)
    assert list(root.iterdir()) == [control]


@pytest.mark.parametrize(
    "classification",
    ["private", "clinical", "unclassified", "controlled_access", "unknown"],
)
def test_fallback_denies_restricted_data_before_any_effect(
    classification, workspace_tmp_path
):
    backend = NonOciPublicFallbackBackend(allow_non_oci_public_fallback=True)
    stage = _stage(workspace_tmp_path)
    with pytest.raises(PermissionError, match="public or synthetic"):
        mounted.run_mounted_container(
            stage, backend=backend, data_classification=classification, **_pins()
        )
    assert not (stage.parent / mounted._INTENT).exists()


@pytest.mark.parametrize(
    ("tier", "purpose"),
    [
        ("L2_statistically_calibrated", "E2"),
        ("L3_scientifically_supported", "exploratory"),
        ("none", "confirmatory"),
        ("L1_execution_verified", "E5"),
        ("L2", "exploratory"),
    ],
)
def test_fallback_disallows_confirmatory_claims(tier, purpose, workspace_tmp_path):
    stage = _stage(workspace_tmp_path)
    backend = NonOciPublicFallbackBackend(allow_non_oci_public_fallback=True)
    with pytest.raises(PermissionError, match="confirmatory"):
        mounted.run_mounted_container(
            stage,
            backend=backend,
            data_classification="synthetic",
            claim_tier=tier,
            epistemic_purpose=purpose,
            **_pins(),
        )
    assert not (stage.parent / mounted._INTENT).exists()


def test_fallback_disabled_and_unknown_worker_fail_closed(workspace_tmp_path):
    stage = _stage(workspace_tmp_path)
    disabled = NonOciPublicFallbackBackend()
    assert isinstance(disabled, ContainerBackend)
    assert (
        disabled.probe()["available"] is False
        and disabled.probe()["oci_isolated"] is False
    )
    with pytest.raises(PermissionError, match="opt-in"):
        mounted.run_mounted_container(
            stage, backend=disabled, data_classification="public", **_pins()
        )
    enabled = NonOciPublicFallbackBackend(allow_non_oci_public_fallback=True)
    with pytest.raises(ValueError, match="registered"):
        mounted.run_mounted_container(
            stage,
            backend=enabled,
            data_classification="public",
            worker_module="unregistered.script",
            **_pins(),
        )


@pytest.mark.parametrize("kind", ["docker", "local"])
def test_live_registered_continuous_worker_outputs_and_cleanup(
    kind, workspace_tmp_path
):
    import json
    import os

    from tests.test_mounted_bulk import _build

    stage = _stage(workspace_tmp_path)
    if kind == "docker":
        image = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE")
        if not image:
            pytest.skip("Set the exact locally installed mounted kernel ID")
        backend = DockerBackend()
    else:
        registry = MountedWorkerRegistry(
            (BUILTIN_WORKERS.worker("rna_kernels.mounted_bulk"),)
        )
        backend = NonOciPublicFallbackBackend(
            allow_non_oci_public_fallback=True, registry=registry
        )
        probe = backend.probe()
        if not probe["available"]:
            pytest.skip("Unprivileged local kernel sandbox unavailable")
        image = probe["runtime_id"]
    request = _build(stage / "inputs", "h5", 256)
    pins = dict(
        image_id=image,
        request_sha=sha256(request.read_bytes()).hexdigest(),
        attempt_id=uuid4().hex,
    )
    receipt = mounted.run_mounted_container(
        stage,
        backend=backend,
        data_classification="synthetic",
        timeout=45,
        memory_mb=2048,
        **pins,
    )
    assert receipt["error_type"] is None and receipt["cleanup_confirmed"]
    assert receipt["oci_isolated"] is (kind == "docker")
    assert receipt["clinical_release_allowed"] is False
    if kind == "local":
        assert receipt["claim_tier"] == "L1_execution_verified"
        assert receipt["isolation_backend"] in {
            "sandbox-exec",
            "bwrap",
            "isolated_subprocess",
        }
    summary = json.loads((stage / "outputs" / "summary.json").read_bytes())
    assert summary["request_sha256"] == pins["request_sha"]
    assert summary["row_count"] == summary["tested_count"] == 256
    if kind == "local":
        assert summary["claim_scope"] == "checked_observation"
    assert mounted.recover_mounted_container(stage, **pins) == {
        k: v for k, v in receipt.items() if k not in {"error_type", "platform"}
    }


def test_mount_admission_rejects_non_regular_inputs(workspace_tmp_path):
    import os

    stage = _stage(workspace_tmp_path)
    os.mkfifo(stage / "inputs" / "stream")
    with pytest.raises(NativeRuntimeError, match="regular files"):
        mounted._prepare_mounts(stage)


def test_cgroup_probe_checks_readback_of_each_new_child_controller(workspace_tmp_path):
    controls = ("cpu.max", "memory.max", "pids.max")
    for name in controls:
        (workspace_tmp_path / name).write_text("max")
    assert backends._constrain_child(workspace_tmp_path)
    assert [(workspace_tmp_path / name).read_text() for name in controls] == [
        "100000 100000",
        "67108864",
        "32",
    ]


def test_discovery_keeps_non_oci_fallback_disabled_by_default(monkeypatch):
    monkeypatch.setattr(
        DockerBackend, "probe", lambda b: {"available": True, "oci_isolated": True}
    )
    monkeypatch.setattr(
        PodmanBackend, "probe", lambda b: {"available": False, "oci_isolated": False}
    )
    observed = backends.probe_container_backends()
    assert observed["docker"]["available"] and not observed["podman"]["available"]
    [fallback] = [
        entry
        for name, entry in observed.items()
        if name not in {"docker", "podman", "apptainer"}
    ]
    assert not fallback["available"] and not fallback["enabled"]
    assert not fallback["oci_isolated"] and not fallback["clinical_release_allowed"]
    assert fallback["max_claim_tier"] == "L1_execution_verified"


def test_recovery_of_a_historical_docker_cleanup_proof(workspace_tmp_path):
    from devbench.engine.tier_a_executor import _write_record
    from tests.test_mounted_oci_recovery import CONTAINER

    stage = _stage(workspace_tmp_path)
    binding = mounted._identity(REQUEST, IMAGE, ATTEMPT)
    intent = {**binding, "endpoint": Docker.endpoint, "daemon_id": "historic"}
    created = {**intent, "container_id": CONTAINER}
    proof = {**created, "disposition": "removed", "cleanup_confirmed": True}
    for name, item in (
        (mounted._INTENT, intent),
        (mounted._CREATED, created),
        (mounted._RECOVERED, proof),
    ):
        _write_record(stage.parent / name, item)
    assert mounted.recover_mounted_container(stage, **_pins()) == proof


def test_mounted_research_inference_requires_oci_even_if_model_allows_local_wald():
    from unittest.mock import Mock

    specification = Mock()
    with pytest.raises(PermissionError, match="pinned OCI image"):
        mounted.require_mounted_pipeline_runtime(
            specification, None, representation="h5", allow_python_wald=True
        )
    specification.require_mounted_runtime.assert_called_once()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("Config.User", "0:0"),
        ("Config.Entrypoint", ["other-worker"]),
        ("Config.Cmd", []),
        ("HostConfig.CapAdd", ["SYS_ADMIN"]),
        ("HostConfig.SecurityOpt", []),
        ("HostConfig.CapDrop", []),
        ("HostConfig.IpcMode", "host"),
        ("HostConfig.PidMode", "host"),
        ("HostConfig.PidsLimit", 0),
        ("HostConfig.Memory", None),
        ("HostConfig.MemorySwap", -1),
        ("HostConfig.NanoCpus", 0),
        ("HostConfig.Tmpfs", {"/tmp": "rw,nosuid,nodev,exec,size=67108864,mode=1777"}),
        ("HostConfig.Tmpfs", {"/tmp": "rw,nosuid,nodev,noexec,size=256m,mode=1777"}),
        ("HostConfig.Devices", [{"PathOnHost": "/dev/synthetic"}]),
        ("HostConfig.Mounts", []),
    ],
)
def test_mounted_refuses_effective_settings_drift_before_start(
    workspace_tmp_path, field, value
):
    stage = _stage(workspace_tmp_path)
    runtime = Docker(stage)
    inspect = runtime.inspect

    def changed(container):
        doc = inspect(container)
        section, key = field.split(".")
        return {**doc, section: {**doc[section], key: value}}

    runtime.inspect = changed
    backend = DockerBackend(runtime_factory=lambda **kw: runtime)
    proof = mounted.run_mounted_container(stage, backend=backend, **_pins())
    assert proof["error_type"] == "NativeRuntimeError" and proof["cleanup_confirmed"]
    assert all(call[0] != "start" for call in runtime.calls)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("UsernsMode", "host"),
        ("IDMappings", {"UidMap": ["0:0:1"], "GidMap": ["0:0:1"]}),
    ],
)
def test_rootless_launch_requires_effective_keep_id_mappings(
    workspace_tmp_path, monkeypatch, key, value
):
    stage = _stage(workspace_tmp_path)
    runtime = _RootlessPodman(stage, workspace_tmp_path)
    inspect = runtime.inspect

    def changed(container):
        doc = inspect(container)
        return {**doc, "HostConfig": {**doc["HostConfig"], key: value}}

    runtime.inspect = changed
    monkeypatch.setattr(backends, "_delegated", lambda host: True)
    backend = PodmanBackend(runtime_factory=lambda authfile: runtime)
    proof = mounted.run_mounted_container(stage, backend=backend, **_pins())
    assert proof["error_type"] == "NativeRuntimeError" and proof["cleanup_confirmed"]
    assert all(call[0] != "start" for call in runtime.calls)
