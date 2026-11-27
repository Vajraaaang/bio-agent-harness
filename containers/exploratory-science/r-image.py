"""Stage public, hash-pinned R archives or build the scientific R image offline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def verify(path: Path, item: dict) -> None:
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_size != item["size_bytes"]
    ):
        raise ValueError(f"Incorrect archive size or type for {path.name}")
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != item["sha256"]:
        raise ValueError(f"SHA-256 mismatch for {path.name}")


def stage(directory: Path, item: dict, *, download: bool) -> None:
    path = directory / item["file"]
    if path.exists() or not download:
        verify(path, item)
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        partial = Path(stream.name)
    try:
        subprocess.run(
            [
                "curl",
                "-q",
                "--fail",
                "--silent",
                "--show-error",
                "--location",
                "--proto",
                "=https",
                "--proto-redir",
                "=https",
                "--retry",
                "3",
                "--max-time",
                "600",
                "--output",
                str(partial),
                item["url"],
            ],
            check=True,
        )
        verify(partial, item)
        partial.chmod(0o400)
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)


def inspect(reference: str) -> dict:
    raw = subprocess.check_output(["docker", "image", "inspect", reference])
    return json.loads(raw)[0]


def build(recipe: Path, directory: Path, arch: str, lock: dict) -> None:
    base_name = f"devbench-rna-kernel:2.0-thesis-routed-{arch}"
    image = f"devharness-explore-r:m1-{arch}"
    base = inspect(base_name)
    if base["Id"] != lock["base_image_config"]:
        raise ValueError(f"Stage the locked local base for {arch}")
    subprocess.run(
        [
            "docker",
            "build",
            "--platform",
            lock["platform"],
            "--network=none",
            "--pull=false",
            "--build-context",
            f"packages={directory}",
            "--build-arg",
            f"KERNEL_IMAGE={base_name}",
            "-f",
            str(recipe / "Dockerfile.r"),
            "-t",
            image,
            str(recipe),
        ],
        check=True,
    )
    result = inspect(image)
    layers = base["RootFS"]["Layers"]
    if (
        result["RootFS"]["Layers"][: len(layers)] != layers
        or result["Architecture"] != arch
    ):
        raise ValueError("Built image differs from the staged native base")
    print(result["Id"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("stage", "verify", "build"))
    parser.add_argument("--arch", choices=("arm64", "amd64"), required=True)
    parser.add_argument(
        "--directory", type=Path, required=True, help="Public archive staging directory"
    )
    args = parser.parse_args()
    if not args.directory.is_absolute():
        parser.error("--directory requires an absolute path")
    directory = args.directory.resolve()
    recipe = Path(__file__).resolve().parent
    lock = json.loads((recipe / f"r-science-{args.arch}.lock.json").read_text())
    archives = [*lock["system_packages"], *lock["r_packages"]]
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(
            pool.map(
                lambda item: stage(directory, item, download=args.action == "stage"),
                archives,
            )
        )
    if args.action == "build":
        build(recipe, directory, args.arch, lock)
    else:
        print(f"Verified {len(archives)} archives for {lock['platform']}")


if __name__ == "__main__":
    main()
