"""P0 image/lock identity checks do not execute R or admit scientific work."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
GUARD_PATH = ROOT / "containers" / "erql-1.1" / "verify-image.py"
IMAGE_ID = "sha256:" + "a" * 64
VERSIONS = {
    "edgeR": "4.10.7",
    "limma": "3.68.5",
    "statmod": "1.5.2",
    "locfit": "1.5-9.12",
    "lattice": "0.23-1",
    "jsonlite": "2.0.0",
}


def _guard():
    assert GUARD_PATH.is_file(), "ERQL image identity guard has not been implemented"
    spec = importlib.util.spec_from_file_location("erql_verify_image", GUARD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _lock():
    return {
        "schema_version": "erql_package_sources/1",
        "profile": "ERQL-1.1",
        "platform": "linux/amd64",
        "R": "4.6.1",
        "BiocVersion": "3.23.1",
        "base_image": "bioconductor/bioconductor_docker@sha256:"
        "979991813693d4256f685a565cd59560d7bca7c4d4d41f66ed5fc38b31338594",
        "targets": list(VERSIONS),
        "packages": [
            {
                "package": name,
                "version": version,
                "file": f"{name}_{version}.tar.gz",
                "url": f"https://example.org/src/contrib/{name}_{version}.tar.gz",
                "sha256": hashlib.sha256(name.encode()).hexdigest(),
                "description_sha256": hashlib.sha256(version.encode()).hexdigest(),
                "bytes": 1234,
                "metadata": {"License": "GPL (>= 2)", "LinkingTo": None},
                "dependencies": [],
            }
            for name, version in VERSIONS.items()
        ],
    }


def _encode(lock):
    return json.dumps(lock, allow_nan=False).encode()


def _inspection(lock_bytes):
    return {
        "Id": IMAGE_ID,
        "Os": "linux",
        "Architecture": "amd64",
        "Config": {
            "Labels": {
                "org.devbench.procedure-profile": "ERQL-1.1",
                "org.devbench.source-lock-sha256": hashlib.sha256(lock_bytes).hexdigest(),
            }
        },
    }


def test_valid_image_identity_has_finite_scoped_receipt():
    lock_bytes = _encode(_lock())
    inspection = _inspection(lock_bytes)
    original = copy.deepcopy(inspection)
    receipt = _guard().validate_image_identity("ERQL-1.1", lock_bytes, IMAGE_ID, inspection)
    assert receipt["schema_version"] == "erql_image_identity/1"
    assert receipt["status"] == "verified"
    assert receipt["profile"] == "ERQL-1.1"
    assert receipt["image_id"] == IMAGE_ID
    assert receipt["platform"] == "linux/amd64"
    assert receipt["source_lock_sha256"] == hashlib.sha256(lock_bytes).hexdigest()
    assert receipt["packages"] == VERSIONS
    assert receipt["verification_scope"] == "docker_image_identity_and_source_lock_only"
    assert json.loads(json.dumps(receipt, allow_nan=False)) == receipt
    assert inspection == original


def test_checked_in_erql_lock_is_accepted_with_matching_synthetic_inspection():
    lock_bytes = (ROOT / "src/devharness_controlled" / "erql-package-sources.lock.json").read_bytes()
    receipt = _guard().validate_image_identity("ERQL-1.1", lock_bytes, IMAGE_ID, _inspection(lock_bytes))
    assert receipt["packages"] == VERSIONS


@pytest.mark.parametrize("profile", ["ERQL-1.0", "erql-1.1", "native_nb", "", None])
def test_unknown_profile_is_rejected(profile):
    lock_bytes = _encode(_lock())
    with pytest.raises(ValueError):
        _guard().validate_image_identity(profile, lock_bytes, IMAGE_ID, _inspection(lock_bytes))


@pytest.mark.parametrize("image_id", ["erql:latest", "sha256:abc", "a" * 64, "sha256:" + "A" * 64,
                                      "repo@sha256:" + "a" * 64, "--help", "", None])
def test_only_full_immutable_image_ids_are_accepted(image_id):
    lock_bytes = _encode(_lock())
    with pytest.raises(ValueError):
        _guard().validate_image_identity("ERQL-1.1", lock_bytes, image_id, _inspection(lock_bytes))


@pytest.mark.parametrize("key,value", [
    ("schema_version", "native_nb_package_sources/1"),
    ("schema_version", "erql_package_sources/2"),
    ("profile", "native_nb"), ("platform", "linux/arm64"),
    ("R", None), ("R", "4.6"), ("BiocVersion", 3.23),
    ("base_image", "bioconductor/bioconductor_docker:RELEASE_3_23"),
    ("targets", ["edgeR"]), ("packages", []), ("packages", {}),
])
def test_invalid_lock_fields_are_rejected_even_with_matching_image_label(key, value):
    lock = _lock()
    lock[key] = value
    lock_bytes = _encode(lock)
    with pytest.raises(ValueError):
        _guard().validate_image_identity("ERQL-1.1", lock_bytes, IMAGE_ID, _inspection(lock_bytes))


@pytest.mark.parametrize("key,value", [
    ("version", "4.10.5"), ("package", "DESeq2"),
    ("file", "../edgeR_4.10.7.tar.gz"), ("file", "/tmp/edgeR_4.10.7.tar.gz"),
    ("file", "..\\edgeR_4.10.7.tar.gz"), ("file", "edgeR_4.10.5.tar.gz"),
    ("sha256", "g" * 64), ("sha256", "a" * 63), ("sha256", None),
    ("description_sha256", "SHA256:" + "a" * 64),
    ("bytes", 0), ("bytes", True), ("bytes", 1.5),
    ("url", "file:///tmp/edgeR_4.10.7.tar.gz"),
    ("url", "https://user:password@example.org/edgeR_4.10.7.tar.gz"),
    ("url", "https://example.org/other.tar.gz"),
    ("url", "https://example.org/edgeR_4.10.7.tar.gz?token=secret"),
    ("metadata", []), ("metadata", {"License": 1}),
    ("dependencies", "limma"), ("dependencies", ["missing"]),
    ("dependencies", ["limma", "limma"]),
])
def test_malformed_package_identity_is_rejected(key, value):
    lock = _lock()
    lock["packages"][0][key] = value
    lock_bytes = _encode(lock)
    with pytest.raises(ValueError):
        _guard().validate_image_identity("ERQL-1.1", lock_bytes, IMAGE_ID, _inspection(lock_bytes))


def test_duplicate_package_is_rejected():
    lock = _lock()
    lock["packages"][1] = copy.deepcopy(lock["packages"][0])
    lock_bytes = _encode(lock)
    with pytest.raises(ValueError):
        _guard().validate_image_identity("ERQL-1.1", lock_bytes, IMAGE_ID, _inspection(lock_bytes))


@pytest.mark.parametrize("lock_bytes", [b"{", b"[]", b"null", b"\xff", b'{"x": NaN}',
                                        b'{"x": 1e999}', b'{"x": 1, "x": 2}', "not bytes"])
def test_malformed_or_ambiguous_json_is_rejected(lock_bytes):
    with pytest.raises(ValueError):
        _guard().validate_image_identity("ERQL-1.1", lock_bytes, IMAGE_ID, {})


def test_even_whitespace_changes_require_a_new_image_lock_label():
    lock_bytes = _encode(_lock())
    with pytest.raises(ValueError):
        _guard().validate_image_identity("ERQL-1.1", lock_bytes + b"\n", IMAGE_ID, _inspection(lock_bytes))


@pytest.mark.parametrize("key,value", [("Id", "sha256:" + "b" * 64), ("Os", "windows"),
                                        ("Architecture", "arm64"), ("Variant", "v8"),
                                        ("Config", None), ("Config", {"Labels": None})])
def test_wrong_or_missing_docker_identity_is_rejected(key, value):
    lock_bytes = _encode(_lock())
    inspection = _inspection(lock_bytes)
    inspection[key] = value
    with pytest.raises(ValueError):
        _guard().validate_image_identity("ERQL-1.1", lock_bytes, IMAGE_ID, inspection)


@pytest.mark.parametrize("key,value", [("org.devbench.procedure-profile", "native_nb"),
                                        ("org.devbench.source-lock-sha256", "0" * 64),
                                        ("org.devbench.source-lock-sha256", None)])
def test_docker_labels_must_bind_the_profile_and_exact_lock(key, value):
    lock_bytes = _encode(_lock())
    inspection = _inspection(lock_bytes)
    inspection["Config"]["Labels"][key] = value
    with pytest.raises(ValueError):
        _guard().validate_image_identity("ERQL-1.1", lock_bytes, IMAGE_ID, inspection)


@pytest.mark.parametrize("inspection", [None, [], "{}", {}, {"Id": IMAGE_ID}])
def test_incomplete_or_malformed_inspection_is_rejected(inspection):
    with pytest.raises(ValueError):
        _guard().validate_image_identity("ERQL-1.1", _encode(_lock()), IMAGE_ID, inspection)


def test_cli_inspects_only_the_immutable_id_and_emits_finite_json(tmp_path, monkeypatch, capsys):
    guard = _guard()
    lock_bytes = _encode(_lock())
    lock_path = tmp_path / "source lock.json"
    lock_path.write_bytes(lock_bytes)
    calls = []

    def inspect(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, json.dumps([_inspection(lock_bytes)]), "")

    monkeypatch.setattr(guard.subprocess, "run", inspect)
    assert guard.main(["--profile", "ERQL-1.1", "--lock", str(lock_path), "--image", IMAGE_ID]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "verified"
    assert len(calls) == 1
    assert calls[0][0] == ["docker", "image", "inspect", IMAGE_ID]
    assert calls[0][1].get("shell", False) is False
    assert calls[0][1]["check"] is True
    assert 0 < calls[0][1]["timeout"] <= 60
    json.dumps(receipt, allow_nan=False)


@pytest.mark.parametrize("docker_output", ["[]", "[{}, {}]", "{}", "not JSON", "[null]"])
def test_cli_rejects_nonunique_or_malformed_inspection(tmp_path, monkeypatch, capsys, docker_output):
    guard = _guard()
    lock_path = tmp_path / "lock.json"
    lock_path.write_bytes(_encode(_lock()))
    monkeypatch.setattr(guard.subprocess, "run", lambda command, **kwargs:
                        subprocess.CompletedProcess(command, 0, docker_output, ""))
    assert guard.main(["--profile", "ERQL-1.1", "--lock", str(lock_path), "--image", IMAGE_ID]) != 0
    assert json.loads(capsys.readouterr().out)["status"] == "rejected"


@pytest.mark.parametrize("failure", [FileNotFoundError("docker"),
    subprocess.CalledProcessError(1, ["docker", "image", "inspect"]),
    subprocess.TimeoutExpired(["docker", "image", "inspect"], 30)])
def test_cli_docker_failure_has_no_fallback(tmp_path, monkeypatch, capsys, failure):
    guard = _guard()
    lock_path = tmp_path / "lock.json"
    lock_path.write_bytes(_encode(_lock()))
    calls = []

    def fail(command, **kwargs):
        calls.append(command)
        raise failure

    monkeypatch.setattr(guard.subprocess, "run", fail)
    assert guard.main(["--profile", "ERQL-1.1", "--lock", str(lock_path), "--image", IMAGE_ID]) != 0
    assert json.loads(capsys.readouterr().out)["status"] == "rejected"
    assert calls == [["docker", "image", "inspect", IMAGE_ID]]


def test_cli_rejects_tags_before_running_docker(tmp_path, monkeypatch, capsys):
    guard = _guard()
    lock_path = tmp_path / "lock.json"
    lock_path.write_bytes(_encode(_lock()))

    def unexpected_inspect(*args, **kwargs):
        pytest.fail("Docker must not run for a mutable tag")

    monkeypatch.setattr(guard.subprocess, "run", unexpected_inspect)
    assert guard.main(["--profile", "ERQL-1.1", "--lock", str(lock_path), "--image", "erql:latest"]) != 0
    assert json.loads(capsys.readouterr().out)["status"] == "rejected"
