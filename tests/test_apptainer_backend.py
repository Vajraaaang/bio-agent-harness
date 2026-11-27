"""Synthetic HPC driver and opt-in checks of an operator-provisioned SIF."""

import json
import os
import shutil
import signal
import subprocess
import sys
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from devbench.engine import container_backend as backends
from devbench.engine import mounted_kernel_oci as mounted
from devbench.engine.container_backend import ApptainerBackend, ContainerBackend
from devbench.engine.tier_a_executor import _write_record
from devharness_controlled.native_runtime import NativeRuntimeError
from tests.test_container_backend import _pins, _stage
from tests.test_mounted_oci_recovery import IMAGE, REQUEST


@pytest.fixture
def image(workspace_tmp_path):
    path = workspace_tmp_path / "kernel.sif"
    raw = bytes(32) + b"SIF_MAGIC\0" + b"01\0" + b"02\0" + b"synthetic payload"
    path.write_bytes(raw)
    return path, {IMAGE: {"path": str(path), "sha256": sha256(raw).hexdigest()}}


@pytest.fixture
def cli(workspace_tmp_path, monkeypatch):
    # This CLI double records launch arguments and answers only the synthetic
    # challenge. Real mount/namespace proof runs in the optional Linux test.
    binary = workspace_tmp_path / "apptainer"
    log = workspace_tmp_path / "launch.jsonl"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        f"log = Path({str(log)!r})\n"
        "args = sys.argv[1:]\n"
        "identity, mounts, module, request, limit = json.loads(args[-1])\n"
        "with log.open('a') as handle:\n"
        "    handle.write(json.dumps([args, list(os.environ)]) + '\\n')\n"
        "outputs = next(v[:-9] for v in args if v.endswith(':/outputs'))\n"
        "if module is None:\n"
        "    (Path(outputs) / 'probe').write_text(request)\n"
        "else:\n"
        "    scratch = next(v[:-5] for v in args if v.endswith(':/tmp'))\n"
        "    (Path(scratch) / '.devbench-isolation').write_text(request)\n"
        "    (Path(outputs) / 'ran').write_text(request)\n"
    )
    binary.chmod(0o700)
    monkeypatch.setattr(backends, "_apptainer_binary", lambda: str(binary))
    original = backends._fs_identity
    monkeypatch.setattr(
        backends,
        "_fs_identity",
        lambda path: [1, 999] if path == "/proc/self/ns/net" else original(path),
    )
    return log


def test_sif_binding_verifies_actual_format_platform_and_bytes(
    image, workspace_tmp_path
):
    path, mapping = image
    backend = ApptainerBackend(images=mapping)
    assert isinstance(backend, ContainerBackend)
    assert backend.verify_image(IMAGE) == "linux/amd64"
    with pytest.raises(NativeRuntimeError, match="No local SIF"):
        backend.verify_image("sha256:" + "1" * 64)
    other = workspace_tmp_path / "other.sif"
    backends._verified_sif(backend._binding(IMAGE), other)
    original = other.read_bytes()
    path.write_bytes(original + b"changed")
    assert other.read_bytes() == original
    with pytest.raises(NativeRuntimeError, match="SHA-256"):
        backend.verify_image(IMAGE)
    path.write_bytes(b"renamed squashfs")
    with pytest.raises(NativeRuntimeError, match="header"):
        backend.verify_image(IMAGE)


@pytest.mark.parametrize(
    "source", ["docker://library/img", "library://test", "./relative.sif"]
)
def test_remote_and_relative_images_rejected(image, source):
    _, mapping = image
    mapping[IMAGE]["path"] = source
    with pytest.raises(NativeRuntimeError, match="local absolute"):
        ApptainerBackend(images=mapping)


@pytest.mark.parametrize("kind", ["symlink", "parent_symlink", "fifo", "directory"])
def test_noncanonical_and_nonregular_images_rejected(image, kind):
    path, mapping = image
    alias = path.with_name("alias.sif")
    if kind == "symlink":
        alias.symlink_to(path)
    elif kind == "parent_symlink":
        directory = path.with_name("alias")
        directory.symlink_to(path.parent, target_is_directory=True)
        alias = directory / path.name
    elif kind == "fifo":
        os.mkfifo(alias)
    else:
        alias.mkdir()
    mapping[IMAGE]["path"] = str(alias)
    with pytest.raises(NativeRuntimeError):
        ApptainerBackend(images=mapping).verify_image(IMAGE)


@pytest.mark.parametrize(
    ("gpu", "flag"), [("cuda", "--nv"), ("rocm", "--rocm"), (None, None)]
)
def test_isolated_launch_receipt_and_recovery(
    image, cli, workspace_tmp_path, monkeypatch, gpu, flag
):
    path, mapping = image
    monkeypatch.setenv("APPTAINER_BINDPATH", "/should-not-pass")
    backend = ApptainerBackend(images=mapping, gpu_backend=gpu)
    assert backend.probe()["local_image_ids"] == [IMAGE]
    stage = _stage(workspace_tmp_path)
    profile = replace(backends.ResourceProfile(), gpu=bool(gpu))
    receipt = mounted.run_mounted_container(
        stage,
        backend=backend,
        worker_module="rna_kernels.mounted_scrna",
        resource_profile=profile,
        data_classification="private",
        **_pins(),
    )
    assert receipt["cleanup_confirmed"] and receipt["error_type"] is None
    assert receipt["clinical_release_allowed"] is False and receipt["oci_isolated"]
    assert (
        receipt["sif_sha256"] == mapping[IMAGE]["sha256"]
        and receipt["image_id"] == IMAGE
    )
    assert receipt["platform"] == "linux/amd64"
    probes = [json.loads(line) for line in cli.read_text().splitlines()]
    argv, env = probes[-1]
    assert len(probes) == 3  # discovery, fresh preflight, checked worker invocation
    assert not any("APPTAINER_BIND" in key for key in env)
    for value in (
        "--containall",
        "--no-home",
        "--cleanenv",
        "--no-eval",
        "--net",
        "--network=none",
    ):
        assert value in argv
    binds = [argv[i + 1] for i, value in enumerate(argv) if value == "--bind"]
    assert len(binds) == 3 and binds[:2] == [
        f"{stage}/inputs:/inputs:ro",
        f"{stage}/outputs:/outputs",
    ]
    assert binds[2].endswith("/scratch:/tmp")
    assert [a for a in argv if a in {"--nv", "--rocm"}] == ([flag] if flag else [])
    assert str(path) not in argv and "--pull" not in argv
    assert (stage / "outputs/ran").read_text() == REQUEST
    assert mounted.recover_mounted_container(stage, **_pins()) == {
        k: v for k, v in receipt.items() if k not in {"platform", "error_type"}
    }
    assert not list(stage.glob(".apptainer-*"))


@pytest.mark.parametrize("classification", ["clinical", "private", "public"])
def test_unverified_namespace_does_not_bind_real_inputs(
    image, cli, workspace_tmp_path, monkeypatch, classification
):
    _, mapping = image
    backend = ApptainerBackend(images=mapping)
    monkeypatch.setattr(backends, "_apptainer_probe", lambda *args: False)
    facts = backend.probe()
    assert (
        not facts["oci_isolated"] and facts["isolation_backend"] == "apptainer_no_netns"
    )
    assert not facts["local_image_ids"]
    stage = _stage(workspace_tmp_path)
    with pytest.raises(NativeRuntimeError, match="network or read-only mount"):
        mounted.run_mounted_container(
            stage, backend=backend, data_classification=classification, **_pins()
        )
    assert not cli.exists() and not (stage.parent / mounted._INTENT).exists()


def test_hpc_selection_requires_trusted_explicit_mapping(image, monkeypatch):
    _, mapping = image
    monkeypatch.setenv("DEVBENCH_APPTAINER_IMAGES", json.dumps(mapping))
    assert isinstance(mounted._default_backend(IMAGE), ApptainerBackend)
    monkeypatch.setattr(backends.shutil, "which", lambda _: None)
    assert isinstance(
        mounted._default_backend("sha256:" + "0" * 64), backends.DockerBackend
    )


@pytest.mark.parametrize(
    ("version", "recognized"),
    [
        (b"singularity-ce version 4.6.0", True),
        (b"apptainer version 1.5.0-rc.1", True),
        (b"unverified singularity version 4.6.0", False),
    ],
)
def test_compatibility_binary_requires_verifiable_identity(
    monkeypatch, version, recognized
):
    monkeypatch.setattr(backends.sys, "platform", "linux")
    monkeypatch.setattr(backends.os, "geteuid", lambda: 1001)
    monkeypatch.setattr(
        backends.shutil,
        "which",
        lambda name: sys.executable if name == "singularity" else None,
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(stdout=version, returncode=0),
    )
    if recognized:
        assert backends._apptainer_binary() == str(Path(sys.executable).resolve())
    else:
        with pytest.raises(NativeRuntimeError, match="unavailable"):
            backends._apptainer_binary()


def test_recovery_fences_retained_process_group_with_birth_check(
    image, workspace_tmp_path
):
    stage = _stage(workspace_tmp_path)
    _, mapping = image
    with subprocess.Popen(
        [sys.executable, "-I", "-c", "import time; time.sleep(60)"],
        start_new_session=True,
    ) as process:
        try:
            intent = {
                **mounted._identity(REQUEST, IMAGE, _pins()["attempt_id"]),
                **backends._APPTAINER_STAMPS,
                "sif_sha256": mapping[IMAGE]["sha256"],
            }
            _write_record(stage.parent / mounted._INTENT, intent)
            _write_record(
                stage.parent / mounted._CREATED,
                {
                    **intent,
                    "pid": process.pid,
                    "process_birth": psutil.Process(process.pid).create_time(),
                },
            )
            with pytest.raises(NativeRuntimeError, match="identity"):
                mounted.recover_mounted_container(
                    stage, **{**_pins(), "request_sha": "1" * 64}
                )
            assert process.poll() is None
            proof = mounted.recover_mounted_container(stage, **_pins())
            assert proof["disposition"] == "terminated" and proof["cleanup_confirmed"]
            with pytest.raises(ProcessLookupError):
                os.killpg(process.pid, 0)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)


def test_live_apptainer_namespace_and_readonly_mounts():
    if not (shutil.which("apptainer") or shutil.which("singularity")):
        pytest.skip("Apptainer/SingularityCE is absent")
    backend = ApptainerBackend()
    if not backend.images:
        pytest.skip("Live probe requires an operator-provisioned, digest-pinned SIF")
    facts = backend.probe()
    assert facts["available"] and facts["oci_isolated"] and facts["local_image_ids"]


@pytest.mark.parametrize(
    "drift", ["network", "interface", "input_writable", "source", "nested", None]
)
def test_guest_preflight_checks_effective_isolation_before_worker(monkeypatch, drift):
    import builtins
    import io
    from unittest.mock import Mock

    mounts = {"/inputs": [2, 10], "/outputs": [2, 11], "/tmp": [2, 12]}
    observed = {**mounts, "/proc/self/ns/net": [3, 100 if drift == "network" else 101]}
    if drift == "source":
        observed["/inputs"] = [2, 999]
    fake_os = SimpleNamespace(
        stat=lambda p: SimpleNamespace(st_dev=observed[p][0], st_ino=observed[p][1]),
        statvfs=lambda p: SimpleNamespace(
            f_flag=int(p == "/inputs" and drift != "input_writable")
        ),
        ST_RDONLY=1,
        environ={},
        execvp=Mock(),
    )
    ifaces = [(1, "lo")]
    if drift == "interface":
        ifaces.append((2, "eth0"))
    deps = {"os": fake_os, "socket": SimpleNamespace(if_nameindex=lambda: ifaces)}
    default_import = builtins.__import__

    def load(name, *args):
        return deps[name] if name in deps else default_import(name, *args)

    paths = [*mounts] + (["/inputs/child"] if drift == "nested" else [])
    lines = "\n".join("1 2 3 4 " + p for p in paths)
    monkeypatch.setattr(
        sys,
        "argv",
        ["-c", json.dumps([[3, 100], mounts, "registered.kernel", REQUEST, 9])],
    )
    namespace = {
        "__builtins__": {
            **vars(builtins),
            "__import__": load,
            "open": lambda *a: io.StringIO(lines),
        }
    }
    if drift:
        with pytest.raises(SystemExit):
            exec(backends._APPTAINER_PREFLIGHT, namespace)
        fake_os.execvp.assert_not_called()
    else:
        exec(backends._APPTAINER_PREFLIGHT, namespace)
        fake_os.execvp.assert_called_once_with(
            "python3.13", ["python3.13", "-m", "registered.kernel", REQUEST]
        )


def test_prelaunch_recovery_after_durable_intent(workspace_tmp_path):
    stage = _stage(workspace_tmp_path)
    intent = {
        **mounted._identity(REQUEST, IMAGE, _pins()["attempt_id"]),
        **backends._APPTAINER_STAMPS,
    }
    _write_record(stage.parent / mounted._INTENT, intent)
    proof = mounted.recover_mounted_container(stage, **_pins())
    assert proof["disposition"] == "not_created" and proof["cleanup_confirmed"]


def test_timeout_reaps_worker_process_group(image, cli, workspace_tmp_path):
    _, mapping = image
    binary = Path(backends._apptainer_binary())
    # Simulate an engine supervising an additional live child past its budget.
    binary.write_text(
        binary.read_text()
        + """
if module is not None:
    import subprocess, time
    subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(30)'])
    time.sleep(30)
"""
    )
    stage = _stage(workspace_tmp_path)
    receipt = mounted.run_mounted_container(
        stage, backend=ApptainerBackend(images=mapping), timeout=1, **_pins()
    )
    assert receipt["error_type"] == "ApptainerError"
    assert receipt["cleanup_confirmed"] and receipt["claim_tier"] == "none"
    with pytest.raises(ProcessLookupError):
        os.killpg(receipt["pid"], 0)


def test_hpc_cancellation_before_launch_discards_only_its_sif_copy(
    image, workspace_tmp_path, monkeypatch
):
    path, mapping = image
    monkeypatch.setenv("DEVBENCH_APPTAINER_IMAGES", json.dumps(mapping))
    stage = _stage(workspace_tmp_path)
    area = stage / (".apptainer-" + _pins()["attempt_id"])
    area.mkdir()
    (area / "runtime.sif").write_bytes(b"partial staged copy")
    proof = mounted.recover_mounted_container(stage, **_pins())
    assert proof["disposition"] == "not_created" and proof["cleanup_confirmed"]
    assert path.is_file() and not area.exists()
    with pytest.raises(NativeRuntimeError, match="disarmed"):
        mounted.run_mounted_container(stage, **_pins())


def test_failed_launch_on_changed_namespace_has_no_isolation_claim(
    image, cli, workspace_tmp_path
):
    _, mapping = image
    binary = Path(backends._apptainer_binary())
    # The preflight on disposable inputs passes. The actual invocation fails
    # before verifying the real mounts and namespace or importing the worker.
    original = binary.read_text()
    binary.write_text(
        original[: original.index("else:\n")] + "else:\n    sys.exit(74)\n"
    )
    stage = _stage(workspace_tmp_path)
    receipt = mounted.run_mounted_container(
        stage,
        backend=ApptainerBackend(images=mapping),
        data_classification="private",
        **_pins(),
    )
    assert receipt["error_type"] and not receipt["oci_isolated"]
    assert receipt["isolation_backend"] == "apptainer_no_netns"
    assert receipt["claim_tier"] == "none" and not (stage / "outputs/ran").exists()
    assert mounted.recover_mounted_container(stage, **_pins())["oci_isolated"] is False


def test_invalid_apptainer_config_keeps_independent_runtime_inventory(monkeypatch):
    monkeypatch.setenv("DEVBENCH_APPTAINER_IMAGES", "invalid")
    monkeypatch.setattr(
        backends.DockerBackend, "probe", lambda self: {"available": True}
    )
    monkeypatch.setattr(
        backends.PodmanBackend, "probe", lambda self: {"available": False}
    )
    inventory = backends.probe_container_backends()
    assert inventory["docker"]["available"] and not inventory["apptainer"]["available"]
    assert not inventory["apptainer"]["clinical_release_allowed"]


@pytest.mark.parametrize("malformed", ["{", "null", "[]"])
def test_unreadable_map_preserves_default_selection(monkeypatch, malformed):
    monkeypatch.setenv("DEVBENCH_APPTAINER_IMAGES", malformed)
    monkeypatch.setattr(backends.shutil, "which", lambda _: None)
    assert backends.configured_apptainer_ids() == set()
    assert isinstance(mounted._default_backend(IMAGE), backends.DockerBackend)


def test_selection_and_discovery_validate_each_binding_independently(
    image, cli, monkeypatch
):
    _, mapping = image
    other = "sha256:" + "e" * 64
    unrelated = "sha256:" + "f" * 64
    monkeypatch.setenv(
        "DEVBENCH_APPTAINER_IMAGES", json.dumps({**mapping, other: None})
    )
    monkeypatch.setattr(backends.shutil, "which", lambda _: None)
    assert backends.configured_apptainer_ids() == {IMAGE, other}
    assert isinstance(mounted._default_backend(unrelated), backends.DockerBackend)
    assert mounted._default_backend(IMAGE).verify_image(IMAGE) == "linux/amd64"
    assert ApptainerBackend().probe()["local_image_ids"] == [IMAGE]
    with pytest.raises(NativeRuntimeError, match="SIF registration"):
        mounted._default_backend(other)
