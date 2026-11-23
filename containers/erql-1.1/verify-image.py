#!/usr/bin/env python3
"""Verify P0 ERQL image/source-lock identity without running R or admitting work."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit


PROFILE = "ERQL-1.1"
PLATFORM = "linux/amd64"
MAX_LOCK_BYTES = 1024 * 1024
PACKAGE_VERSIONS = {
    "edgeR": "4.10.7",
    "limma": "3.68.5",
    "statmod": "1.5.2",
    "locfit": "1.5-9.12",
    "lattice": "0.23-1",
    "jsonlite": "2.0.0",
}


def _matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def _json(data):
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("nonfinite JSON number")
        return number

    def reject_constant(value):
        raise ValueError("nonfinite JSON constant")

    try:
        return json.loads(data, object_pairs_hook=object_pairs,
                          parse_float=finite_float, parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("invalid JSON") from exc


def _safe_source_url(value, filename):
    if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 for c in value):
        return False
    try:
        url = urlsplit(value)
        segments = url.path.split("/")[1:]
        return (url.scheme == "https" and bool(url.hostname)
                and url.username is None and url.password is None
                and not url.query and not url.fragment
                and bool(segments) and segments[-1] == filename
                and all(_matches(r"[A-Za-z0-9._-]+", part)
                        and part not in {".", ".."} for part in segments))
    except ValueError:
        return False


def _validate_source_lock(profile, lock_bytes):
    if profile != PROFILE:
        raise ValueError("unsupported procedure profile")
    if not isinstance(lock_bytes, bytes) or not 0 < len(lock_bytes) <= MAX_LOCK_BYTES:
        raise ValueError("source lock must be bounded nonempty bytes")
    lock = _json(lock_bytes)
    if not isinstance(lock, dict) or lock.get("schema_version") != "erql_package_sources/1":
        raise ValueError("unsupported source lock schema")
    if lock.get("profile") != profile or lock.get("platform") != PLATFORM:
        raise ValueError("source lock profile or platform mismatch")
    if not all(_matches(r"[0-9]+\.[0-9]+\.[0-9]+", lock.get(key)) for key in ("R", "BiocVersion")):
        raise ValueError("invalid R or Bioconductor source identity")
    if not _matches(r"bioconductor/bioconductor_docker@sha256:[0-9a-f]{64}", lock.get("base_image")):
        raise ValueError("source lock requires an immutable Bioconductor base image")
    targets = lock.get("targets")
    if (not isinstance(targets, list) or not all(isinstance(name, str) for name in targets)
            or len(targets) != len(PACKAGE_VERSIONS) or set(targets) != set(PACKAGE_VERSIONS)):
        raise ValueError("source lock targets do not match the ERQL package set")
    packages = lock.get("packages")
    if not isinstance(packages, list) or len(packages) != len(PACKAGE_VERSIONS):
        raise ValueError("source lock must contain the complete ERQL package set")
    seen = set()
    for package in packages:
        if not isinstance(package, dict):
            raise ValueError("invalid source package row")
        name = package.get("package")
        if not isinstance(name, str) or name not in PACKAGE_VERSIONS or name in seen:
            raise ValueError("unknown or duplicate source package")
        seen.add(name)
        version = PACKAGE_VERSIONS[name]
        if package.get("version") != version:
            raise ValueError(f"source package version mismatch: {name}")
        filename = f"{name}_{version}.tar.gz"
        if package.get("file") != filename:
            raise ValueError(f"unsafe or mismatched source filename: {name}")
        if not all(_matches(r"[0-9a-f]{64}", package.get(key))
                   for key in ("sha256", "description_sha256")):
            raise ValueError(f"invalid source digest: {name}")
        size = package.get("bytes")
        if type(size) is not int or size <= 0:
            raise ValueError(f"invalid source size: {name}")
        if not _safe_source_url(package.get("url"), filename):
            raise ValueError(f"unsafe or mismatched source URL: {name}")
        metadata = package.get("metadata")
        if (not isinstance(metadata, dict) or not metadata
                or not all(isinstance(key, str) and key
                           and (value is None or isinstance(value, str))
                           for key, value in metadata.items())
                or not isinstance(metadata.get("License"), str) or not metadata["License"].strip()):
            raise ValueError(f"invalid source metadata: {name}")
        dependencies = package.get("dependencies")
        if (not isinstance(dependencies, list)
                or not all(isinstance(item, str) and item in PACKAGE_VERSIONS and item != name
                           for item in dependencies)
                or len(set(dependencies)) != len(dependencies)):
            raise ValueError(f"invalid source dependencies: {name}")
    return lock


def _validate_image_id(image_id):
    if not _matches(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError("image must be a full immutable sha256 image ID")


def validate_image_identity(profile, lock_bytes, image_id, inspection):
    """Return identity evidence or raise ValueError; no IO or runtime qualification."""
    _validate_image_id(image_id)
    lock = _validate_source_lock(profile, lock_bytes)
    if not isinstance(inspection, dict) or inspection.get("Id") != image_id:
        raise ValueError("Docker inspected image ID does not match the requested image")
    if (inspection.get("Os") != "linux" or inspection.get("Architecture") != "amd64"
            or inspection.get("Variant") not in (None, "")):
        raise ValueError("Docker inspected platform must be linux/amd64")
    config = inspection.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    lock_sha256 = hashlib.sha256(lock_bytes).hexdigest()
    if (not isinstance(labels, dict) or labels.get("org.devbench.procedure-profile") != profile
            or labels.get("org.devbench.source-lock-sha256") != lock_sha256):
        raise ValueError("Docker labels do not bind the requested profile and exact source lock")
    return {
        "schema_version": "erql_image_identity/1",
        "status": "verified",
        "verification_scope": "docker_image_identity_and_source_lock_only",
        "profile": profile,
        "image_id": image_id,
        "platform": PLATFORM,
        "source_lock_sha256": lock_sha256,
        "base_image": lock["base_image"],
        "R": lock["R"],
        "BiocVersion": lock["BiocVersion"],
        "packages": dict(PACKAGE_VERSIONS),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--lock", required=True)
    parser.add_argument("--image", required=True)
    args = parser.parse_args(argv)
    try:
        _validate_image_id(args.image)
        with Path(args.lock).expanduser().resolve().open("rb") as source:
            lock_bytes = source.read(MAX_LOCK_BYTES + 1)
        _validate_source_lock(args.profile, lock_bytes)
        result = subprocess.run(["docker", "image", "inspect", args.image],
                                check=True, capture_output=True, text=True, timeout=30)
        inspected = _json(result.stdout)
        if not isinstance(inspected, list) or len(inspected) != 1:
            raise ValueError("Docker inspection must return exactly one image")
        receipt = validate_image_identity(args.profile, lock_bytes, args.image, inspected[0])
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        receipt = {
            "schema_version": "erql_image_identity/1",
            "status": "rejected",
            "verification_scope": "docker_image_identity_and_source_lock_only",
            "error": str(exc) if isinstance(exc, ValueError) else "source lock read or Docker inspection failed",
        }
        print(json.dumps(receipt, allow_nan=False, sort_keys=True))
        return 1
    print(json.dumps(receipt, allow_nan=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
