"""Resource contracts for hashing and preserving small synthetic inputs."""
from pathlib import Path

import pytest

from devharness_core import provenance as p
from devharness_controlled import executor as e


@pytest.mark.parametrize("directory", [False, True])
def test_bounded_identity_matches_existing_format(tmp_path, directory):
    source = tmp_path / "source"
    if directory:
        source.mkdir()
        (source / "empty").mkdir()
        (source / "a").write_bytes(b"12345678")
        (source / "b").write_bytes(b"abcdefgh")
    else:
        source.write_bytes(b"12345678abcdefgh")
    expected = p.hash_artifact(source)
    assert p.hash_artifact(source, max_bytes=16, max_entries=3) == expected
    destination = tmp_path / "preserved"
    actual = p.copy_artifact(source, destination, max_bytes=16, max_entries=3)
    assert actual == p.hash_artifact(destination)
    assert {k:v for k,v in actual.items() if k != "name"} == {k:v for k,v in expected.items() if k != "name"}


@pytest.mark.parametrize("limit", [0, 15])
def test_static_bytes_over_cap_are_not_copied(tmp_path, limit):
    source = tmp_path / "input"
    source.write_bytes(b"x" * 16)
    target = tmp_path / "copy"
    with pytest.raises(ValueError, match="byte limit"):
        p.copy_artifact(source, target, max_bytes=limit, max_entries=1)
    assert not target.exists()


@pytest.mark.parametrize("option,value", [("max_bytes", True), ("max_bytes", -1),
    ("max_bytes", 1.5), ("max_entries", False), ("max_entries", -1), ("max_entries", "2")])
def test_invalid_limits_are_rejected_before_destination_creation(tmp_path, option, value):
    source = tmp_path / "input"
    source.write_bytes(b"x")
    target = tmp_path / "copy"
    with pytest.raises(ValueError, match=option):
        p.copy_artifact(source, target, **({"max_bytes":16, "max_entries":3} | {option:value}))
    assert not target.exists()


def test_empty_artifacts_and_entry_limit_semantics(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert p.hash_artifact(empty, max_bytes=0, max_entries=0) == p.hash_artifact(empty)
    source = tmp_path / "input"
    source.touch()
    with pytest.raises(ValueError, match="entry limit"):
        p.hash_artifact(source, max_bytes=0, max_entries=0)
    assert p.hash_artifact(source, max_bytes=0, max_entries=1) == p.hash_artifact(source)


@pytest.mark.parametrize("operation", ["hash", "copy"])
def test_total_byte_budget_spans_all_files(tmp_path, operation):
    source = tmp_path / "tree"
    source.mkdir()
    for name in ("a", "b", "c"):
        (source / name).write_bytes(b"12345678")
    with pytest.raises(ValueError, match="byte limit"):
        if operation == "hash":
            p.hash_artifact(source, max_bytes=16, max_entries=3)
        else:
            p.copy_artifact(source, tmp_path / "copy", max_bytes=16, max_entries=3)


def test_inventory_cap_stops_before_reading_payload(tmp_path, monkeypatch):
    source = tmp_path / "tree"
    source.mkdir()
    for name in ("a", "b", "c"):
        (source / name).touch()
    def forbidden(*args, **kwargs):
        pytest.fail("Payload reader reached after entry cap exceeded")
    monkeypatch.setattr(p, "_open_regular", forbidden)
    with pytest.raises(ValueError, match="entry limit"):
        p.hash_artifact(source, max_bytes=0, max_entries=2)


@pytest.mark.parametrize("operation", ["hash", "copy"])
def test_growth_during_read_uses_only_one_extra_byte_and_bounded_output(tmp_path, monkeypatch, operation):
    source = tmp_path / "input"
    source.write_bytes(b"a" * 8)
    target = tmp_path / "copy"
    original = p.os.read
    sizes = []
    def read_then_append(fd, size):
        data = original(fd, size)
        sizes.append(len(data))
        if len(sizes) == 1:
            with source.open("ab") as stream:
                stream.write(b"b" * 25)
        return data
    monkeypatch.setattr(p.os, "read", read_then_append)
    with pytest.raises(ValueError, match="byte limit"):
        if operation == "hash":
            p.hash_artifact(source, max_bytes=16, max_entries=1)
        else:
            p.copy_artifact(source, target, max_bytes=16, max_entries=1)
    assert source.stat().st_size == 33
    assert sum(sizes) <= 17
    assert not target.exists() or target.stat().st_size <= 16


def test_controlled_snapshot_bounds_growth_after_initial_hash(tmp_path, monkeypatch):
    source = tmp_path / "input"
    source.write_bytes(b"a" * 8)
    expected = p.hash_artifact(source)["sha256"]
    original = e.hash_artifact
    calls = []
    def hash_then_append(path, **options):
        result = original(path, **options)
        if Path(path) == source and not calls:
            with source.open("ab") as stream:
                stream.write(b"b" * 25)
            calls.append(True)
        return result
    monkeypatch.setattr(e, "hash_artifact", hash_then_append)
    target = tmp_path / "copy"
    with pytest.raises(ValueError):
        e._snapshot(source, target, expected, 16)
    assert calls and (not target.exists() or target.stat().st_size <= 16)


def test_controlled_inventory_growth_is_rejected_before_copy(tmp_path, monkeypatch):
    source = tmp_path / "tree"
    source.mkdir()
    for i in range(10001):
        (source / str(i)).touch()
    expected = p.hash_artifact(source)["sha256"]
    for i in range(1,10001):
        (source / str(i)).unlink()
    original = e.hash_artifact
    changed = []
    def expand_before_hash(path, **options):
        if Path(path) == source and not changed:
            for i in range(1,10001):
                (source / str(i)).touch()
            changed.append(True)
        return original(path, **options)
    monkeypatch.setattr(e, "hash_artifact", expand_before_hash)
    destination = tmp_path / "copy"
    with pytest.raises(ValueError, match="limit"):
        e._snapshot(source, destination, expected, 16)
    assert changed and not destination.exists()


def test_copy_never_overwrites_and_rejects_overlap(tmp_path):
    source = tmp_path / "input"
    source.write_bytes(b"data")
    target = tmp_path / "existing"
    target.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        p.copy_artifact(source, target, max_bytes=8, max_entries=2)
    assert target.read_bytes() == b"keep"
    tree = tmp_path / "tree"
    tree.mkdir()
    with pytest.raises(ValueError, match="overlap"):
        p.copy_artifact(tree, tree / "copy", max_bytes=8, max_entries=2)
    assert not (tree / "copy").exists()


@pytest.mark.parametrize("kind", ["link", "fifo"])
def test_bounded_copy_refuses_nonregular_inputs(tmp_path, kind):
    source = tmp_path / "tree"
    source.mkdir()
    if kind == "link":
        (source / "member").symlink_to(tmp_path / "outside")
    else:
        p.os.mkfifo(source / "member")
    with pytest.raises(ValueError, match="[Ss]ymlink|regular"):
        p.copy_artifact(source, tmp_path / "copy", max_bytes=16, max_entries=2)


def test_concurrent_directory_copies_reserve_one_destination(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Lock

    sources = [tmp_path / "first", tmp_path / "second"]
    for source, name in zip(sources, ("a", "b")):
        source.mkdir()
        (source / name).write_bytes(name.encode())
    target = tmp_path / "copy"
    barrier, lock, observations = Barrier(2, timeout=3), Lock(), []
    original = Path.exists
    def simultaneous_absence(path):
        exists = original(path)
        if path == target:
            with lock:
                synchronize = len(observations) < 2
                if synchronize:
                    observations.append(exists)
            if synchronize:
                barrier.wait()
        return exists
    monkeypatch.setattr(Path, "exists", simultaneous_absence)
    def copy(source):
        try:
            return p.copy_artifact(source, target, max_bytes=1, max_entries=1)
        except FileExistsError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(copy, source) for source in sources]
        outcomes = [future.result(timeout=5) for future in futures]
    assert observations == [False, False]
    successful = [value for value in outcomes if value is not None]
    assert len(successful) == 1
    assert p.hash_artifact(target) == successful[0]
    assert successful[0]["size_bytes"] == len(successful[0]["entries"]) == 1
