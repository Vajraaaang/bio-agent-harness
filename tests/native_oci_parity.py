"""Nightly native mounted-worker execution and cross-runner ADR-16 comparison."""

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

from devbench.analysis.thesis_replay import compare_replayed_fit
from devbench.engine.container_backend import DockerBackend, PodmanBackend
from devbench.engine.mounted_kernel_oci import run_mounted_container
from rna_kernels.numerical_equivalence import CROSS_ARCH, ReplayRuntime
from tests.test_mounted_bulk import _build


def create_inputs(directory):
    directory.mkdir(parents=True)
    _build(directory, "h5", 256)


def run_native(engine, image, inputs, result):
    backend = {"docker": DockerBackend, "podman": PodmanBackend}[engine]()
    target = backend.verify_image(image)
    host = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}[platform.machine()]
    if target != f"linux/{host}" or not backend.probe()["oci_isolated"]:
        raise RuntimeError(f"Native isolation unavailable for {engine} on {target}")
    stage = result / "stage"
    source = stage / "inputs"
    shutil.copytree(inputs, source)
    output = stage / "outputs"
    output.mkdir()
    request_hash = hashlib.sha256((source / "request.json").read_bytes()).hexdigest()
    proof = run_mounted_container(
        stage,
        backend=backend,
        image_id=image,
        request_sha=request_hash,
        attempt_id=uuid4().hex,
        timeout=120,
        memory_mb=2048,
        data_classification="synthetic",
    )
    if (
        proof["error_type"]
        or not proof["cleanup_confirmed"]
        or not proof["oci_isolated"]
        or proof["isolation_backend"] != engine
        or proof["clinical_release_allowed"] is not False
    ):
        raise RuntimeError(f"{engine} failed the mounted execution check: {proof}")
    # Read software versions from the same immutable image used for the fit.
    versions = subprocess.check_output(
        [
            engine,
            "run",
            "--rm",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--entrypoint",
            "python3.13",
            image,
            "-c",
            "import json, importlib.metadata as m; "
            "print(json.dumps({k: m.version(k) for k in "
            "('numpy', 'scipy', 'h5py', 'pyarrow')}))",
        ],
        timeout=30,
    )
    runtime = {
        "arch": target,
        "image_digest": image,
        "package_versions": json.loads(versions),
        "blas_threads": None,
    }
    for name in ("summary.json", "genes.parquet"):
        shutil.copyfile(output / name, result / name)
    (result / "runtime.json").write_text(json.dumps(runtime))
    (result / "isolation.json").write_text(json.dumps(proof))


def compare(left, right):
    def runtime(path):
        return ReplayRuntime(**json.loads((path / "runtime.json").read_bytes()))

    receipt = compare_replayed_fit(
        left,
        right,
        mode=CROSS_ARCH,
        source_runtime=runtime(left),
        target_runtime=runtime(right),
    )
    print(json.dumps(receipt, allow_nan=False))
    if not receipt["passed"]:
        raise SystemExit("Native mounted fits failed ADR-16")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    inputs = commands.add_parser("inputs")
    inputs.add_argument("directory", type=Path)
    run = commands.add_parser("run")
    run.add_argument("engine", choices=["docker", "podman"])
    run.add_argument("image")
    run.add_argument("inputs", type=Path)
    run.add_argument("result", type=Path)
    check = commands.add_parser("compare")
    check.add_argument("left", type=Path)
    check.add_argument("right", type=Path)
    args = parser.parse_args()
    if args.action == "inputs":
        create_inputs(args.directory.resolve())
    elif args.action == "run":
        run_native(
            args.engine, args.image, args.inputs.resolve(), args.result.resolve()
        )
    else:
        compare(args.left.resolve(), args.right.resolve())


if __name__ == "__main__":
    main()
