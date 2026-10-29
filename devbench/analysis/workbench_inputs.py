"""Bounded read-only input discovery and supervised metadata profiles.

File identity and header observations do not establish experimental provenance,
raw counts, independent biological units, a valid design or a supported method.
No input is copied, changed, uploaded, fitted or sent to a model by this module.
"""
from __future__ import annotations

from contextlib import contextmanager
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading


SUFFIXES = {".h5ad", ".json", ".csv", ".tsv"}
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


def _signature(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)


def _directory(path):
    """Open every absolute directory component without following symlinks."""
    descriptor = os.open("/", _DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, _DIRECTORY, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


@contextmanager
def _open_file(root, parts, root_identity):
    directory = _directory(Path(root))
    descriptor = None
    try:
        actual = os.fstat(directory)
        if (actual.st_dev, actual.st_ino) != tuple(root_identity):
            raise ValueError("Configured input root identity changed; restart with reviewed roots")
        for part in parts[:-1]:
            child = os.open(part, _DIRECTORY, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(parts[-1], _FILE, dir_fd=directory)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Input must be a regular file, not a link, device or pipe")
        yield descriptor
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


def _hash_fd(descriptor, maximum):
    before = os.fstat(descriptor)
    if before.st_size > maximum:
        raise ValueError("Input exceeds the configured hashing byte budget")
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    total = 0
    while True:
        block = os.read(descriptor, min(1024 * 1024, maximum - total + 1))
        if not block:
            break
        total += len(block)
        if total > maximum:
            raise ValueError("Input changed or exceeds its hashing byte budget")
        digest.update(block)
    if total != before.st_size or _signature(os.fstat(descriptor)) != _signature(before):
        raise ValueError("Input changed during identity inspection")
    return digest.hexdigest()


def _unchanged(root, parts, root_identity, expected):
    with _open_file(root, parts, root_identity) as descriptor:
        if _signature(os.fstat(descriptor)) != tuple(expected):
            raise ValueError("Input changed during inspection; refresh before using this profile")


def _names(group, maximum):
    names = []
    for name in group:
        if len(names) >= maximum or not isinstance(name, str) or not 1 <= len(name) <= 200:
            raise ValueError("Input metadata names exceed the header bounds")
        names.append(name)
    return names


def _h5ad_header(handle, maximum):
    import h5py

    def node(group, name):
        link = group.get(name, getlink=True)
        if link is None:
            raise ValueError("Required AnnData header is missing: " + name)
        if not isinstance(link, h5py.HardLink):
            raise ValueError("External and symbolic HDF5 links are not permitted in inspected headers")
        return group[name]

    def attr(group, name, limit):
        if name not in group.attrs:
            return None
        shape = group.attrs.get_id(name).shape
        count = 1
        for dimension in shape:
            count *= dimension
        if count > limit:
            raise ValueError("AnnData header attribute exceeds the metadata budget")
        return group.attrs[name]

    def plain(value):
        result = value.decode("utf-8") if isinstance(value, bytes) else str(value)
        if not 1 <= len(result) <= 200:
            raise ValueError("AnnData header label exceeds the metadata budget")
        return result

    def dataframe(name):
        group = node(handle, name)
        if not isinstance(group, h5py.Group):
            raise ValueError("AnnData observation/feature headers must be groups")
        available = _names(group, maximum + 1)
        for column in available:
            node(group, column)  # Reject links without materializing any values.
        index_name = plain(attr(group, "_index", 1) or "_index")
        index = node(group, index_name)
        if not isinstance(index, h5py.Dataset) or len(index.shape) != 1:
            raise ValueError("AnnData identity header must have one dimension")
        order = attr(group, "column-order", maximum)
        columns = [plain(value) for value in order] if order is not None else [value for value in available if value != index_name]
        if (len(columns) > maximum or len(set(columns)) != len(columns)
                or any(value not in available or value == index_name for value in columns)):
            raise ValueError("AnnData column header names are inconsistent")
        return int(index.shape[0]), columns

    matrix = node(handle, "X")
    if isinstance(matrix, h5py.Dataset):
        shape = matrix.shape
    elif isinstance(matrix, h5py.Group):
        shape = attr(matrix, "shape", 2)
        if shape is None:
            raise ValueError("Sparse AnnData matrix shape header is missing")
    else:
        raise ValueError("AnnData X must have a matrix shape header")
    if len(shape) != 2 or any(int(value) < 1 for value in shape):
        raise ValueError("AnnData matrix shape must be nonempty and two-dimensional")
    cells, obs_columns = dataframe("obs")
    genes, var_columns = dataframe("var")
    if (cells, genes) != tuple(int(value) for value in shape):
        raise ValueError("AnnData shape and observation/feature headers disagree")
    def members(name):
        if name not in handle:
            return []
        group = node(handle, name)
        if not isinstance(group, h5py.Group):
            raise ValueError("AnnData named metadata collection must be a group")
        names = _names(group, maximum)
        for member in names:
            node(group, member)
        return names
    return {"cells": cells, "genes": genes, "obs_columns": obs_columns,
        "var_columns": var_columns, "layers": members("layers"), "obsm_keys": members("obsm"),
        "header_status": "inspected", "annotation_values_read": False}


def _table_header(source, suffix, header_bytes, maximum):
    remaining = header_bytes
    def lines():
        nonlocal remaining
        while remaining > 0:
            line = source.readline(remaining + 1)
            if not line:
                return
            if len(line) > remaining:
                raise ValueError("Table header exceeds the bounded header byte budget")
            remaining -= len(line)
            yield line.decode("utf-8-sig")
        raise ValueError("Table header exceeds the bounded header byte budget")
    columns = next(csv.reader(lines(), delimiter="\t" if suffix == ".tsv" else ",", strict=True), [])
    if not columns or len(columns) > maximum or any(not 1 <= len(value) <= 200 for value in columns):
        raise ValueError("Table header names exceed the metadata bounds")
    return {"columns": columns, "column_names_unique": len(set(columns)) == len(columns),
        "rows": None, "header_only": True, "header_status": "inspected"}


def _json_header(source, size, header_bytes, maximum):
    if size > header_bytes:
        return {"top_level_keys": [], "header_status": "not_inspected_byte_budget"}
    def pairs(items):
        if len(items) > maximum or len({key for key, _ in items}) != len(items):
            raise ValueError("JSON object has duplicate or excessive field names")
        return dict(items)
    def invalid(_value):
        raise ValueError("JSON metadata must use finite values")
    value = json.loads(source.read(header_bytes + 1), object_pairs_hook=pairs, parse_constant=invalid)
    names = list(value) if isinstance(value, dict) else []
    if any(not isinstance(name, str) or not 1 <= len(name) <= 200 for name in names):
        raise ValueError("JSON field names exceed the metadata bounds")
    return {"top_level_keys": names, "json_container": "object" if isinstance(value, dict) else "array" if isinstance(value, list) else "scalar",
        "header_status": "inspected", "supplied_format": value.get("format") if isinstance(value, dict) and isinstance(value.get("format"), str) and len(value["format"]) <= 200 else None}


def _inspect_worker(root, parts, root_identity, expected, limits):
    """Trusted importable resource-isolated worker; opens only the bound input."""
    from .resources import configure_threads
    configure_threads()
    suffix = Path(parts[-1]).suffix.lower()
    with _open_file(root, parts, root_identity) as descriptor:
        if _signature(os.fstat(descriptor)) != tuple(expected):
            raise ValueError("Input changed before supervised inspection")
        identity = _hash_fd(descriptor, limits["max_file_bytes"])
        observed = {"file_type": suffix[1:], "cells": None, "genes": None,
            "obs_columns": [], "var_columns": [], "layers": [], "obsm_keys": [], "expression_values_read": False}
        os.lseek(descriptor, 0, os.SEEK_SET)
        with os.fdopen(os.dup(descriptor), "rb") as source:
            if suffix == ".h5ad":
                import h5py
                with h5py.File(source, "r") as handle:
                    observed.update(_h5ad_header(handle, limits["max_columns"]))
            elif suffix in {".csv", ".tsv"}:
                observed.update(_table_header(source, suffix, limits["header_bytes"], limits["max_columns"]))
            else:
                observed.update(_json_header(source, expected[3], limits["header_bytes"], limits["max_columns"]))
        if _signature(os.fstat(descriptor)) != tuple(expected):
            raise ValueError("Input changed during supervised header inspection")
    _unchanged(root, parts, root_identity, expected)
    return {"sha256": identity, "observed": observed}


def _candidates(columns):
    def matches(words):
        return [column for column in columns if re.sub(r"[^a-z0-9]", "_", column.lower()).strip("_") in words]
    return {"batch": matches({"batch", "batch_id", "capture_batch", "library", "library_id"}),
        "capture_unit": matches({"batch", "batch_id", "capture_batch", "library", "library_id", "capture", "capture_unit", "capture_unit_id", "sample", "sample_id"}),
        "donor": matches({"donor", "donor_id", "subject", "subject_id", "patient", "patient_id", "animal", "animal_id", "individual", "individual_id", "mouse", "mouse_id"})}


class InputDiscovery:
    """Discover only explicitly configured roots; cache by revalidated file identity."""

    def __init__(self, input_roots, *, max_depth=2, max_files=64, max_entries=512,
                 max_file_bytes=4 * 1024**3, list_hash_bytes=32 * 1024**2,
                 header_bytes=256 * 1024, max_columns=128, timeout_seconds=20, memory_mb=512):
        values = {"max_depth": (max_depth, 0, 4), "max_files": (max_files, 1, 256),
            "max_entries": (max_entries, 1, 4096), "max_file_bytes": (max_file_bytes, 1, 16 * 1024**3),
            "list_hash_bytes": (list_hash_bytes, 1, 64 * 1024**2), "header_bytes": (header_bytes, 1, 1024**2),
            "max_columns": (max_columns, 1, 256), "timeout_seconds": (timeout_seconds, 1, 60), "memory_mb": (memory_mb, 64, 1024)}
        if any(type(value) is not int or not low <= value <= high for value, low, high in values.values()):
            raise ValueError("Input discovery limits must stay within bounded integer resource ceilings")
        self.limits = {name: value[0] for name, value in values.items()}
        self._roots = {}
        for supplied in input_roots:
            root = Path(supplied)
            if not root.is_absolute() or ".." in root.parts or root == Path("/") or root != root.resolve(strict=True):
                raise ValueError("Input roots must be explicit canonical directories without symlinks")
            descriptor = _directory(root)
            try:
                metadata = os.fstat(descriptor)
                self._roots[root] = (metadata.st_dev, metadata.st_ino)
            finally:
                os.close(descriptor)
        if not self._roots:
            raise ValueError("Configure at least one existing input root")
        self._hashes, self._profiles = {}, {}
        self._lock = threading.RLock()

    def _source(self, supplied):
        if not isinstance(supplied, (str, Path)):
            raise ValueError("Supply an absolute canonical input path")
        path = Path(supplied)
        if not path.is_absolute() or ".." in path.parts or path != path.resolve(strict=True):
            raise ValueError("Input path must be canonical and cannot traverse symlinks")
        roots = [root for root in self._roots if path.is_relative_to(root) and path != root]
        if not roots:
            raise PermissionError("Input is outside configured input roots")
        if path.suffix.lower() not in SUFFIXES:
            raise ValueError("Input format is unavailable for bounded discovery")
        root = max(roots, key=lambda value: len(value.parts))
        return path, root, path.relative_to(root).parts, self._roots[root]

    def _cache(self, cache, path, signature, value):
        if path not in cache and len(cache) >= self.limits["max_files"]:
            cache.pop(next(iter(cache)))
        cache[path] = (signature, value)

    def source_path(self, source):
        """Admit supported regular-file metadata without hashing or parsing data.

        Investigation registration uses this broader input boundary; the legacy
        H5AD scientific execution reader remains a separate, narrower contract.
        Later inspection/execution must still bind and verify content identity.
        """
        with self._lock:
            path, root, parts, identity = self._source(source)
            with _open_file(root, parts, identity) as descriptor:
                signature = _signature(os.fstat(descriptor))
                if signature[3] > self.limits["max_file_bytes"]:
                    raise ValueError("Input exceeds the configured inspection byte budget")
            _unchanged(root, parts, identity, signature)
            return path

    def list(self):
        with self._lock:
            paths, seen, truncated = [], set(), False
            entries = 0
            def scan(root, descriptor, relative=(), depth=0):
                nonlocal entries, truncated
                with os.scandir(descriptor) as listing:
                    for item in listing:
                        entries += 1
                        if entries > self.limits["max_entries"] or len(paths) >= self.limits["max_files"]:
                            truncated = True
                            return
                        metadata = item.stat(follow_symlinks=False)
                        parts = relative + (item.name,)
                        if stat.S_ISDIR(metadata.st_mode) and depth < self.limits["max_depth"]:
                            child = os.open(item.name, _DIRECTORY, dir_fd=descriptor)
                            try:
                                opened = os.fstat(child)
                                if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                                    raise ValueError("Input directory changed during discovery")
                                scan(root, child, parts, depth + 1)
                            finally:
                                os.close(child)
                        elif stat.S_ISREG(metadata.st_mode) and Path(item.name).suffix.lower() in SUFFIXES:
                            path = root.joinpath(*parts)
                            if path not in seen:
                                paths.append(path)
                                seen.add(path)
            for root, identity in self._roots.items():
                descriptor = _directory(root)
                try:
                    actual = os.fstat(descriptor)
                    if (actual.st_dev, actual.st_ino) != identity:
                        raise ValueError("Configured input root identity changed; restart with reviewed roots")
                    scan(root, descriptor)
                finally:
                    os.close(descriptor)
            rows, remaining = [], self.limits["list_hash_bytes"]
            for source in sorted(paths):
                path, root, parts, identity = self._source(source)
                with _open_file(root, parts, identity) as descriptor:
                    signature = _signature(os.fstat(descriptor))
                    cached = self._hashes.get(path)
                    digest = cached[1] if cached and cached[0] == signature else None
                    if digest is None and signature[3] <= min(remaining, self.limits["max_file_bytes"]):
                        digest = _hash_fd(descriptor, self.limits["max_file_bytes"])
                        remaining -= signature[3]
                        self._cache(self._hashes, path, signature, digest)
                _unchanged(root, parts, identity, signature)
                row = {"source": str(path), "path": str(path), "filename": path.name, "size_bytes": signature[3],
                    "sha256": digest, "hash_status": "ready" if digest else "pending_budget"}
                profile = self._profiles.get(path)
                if profile and profile[0] == signature:
                    row["profile"] = json.loads(json.dumps(profile[1]))
                rows.append(row)
            return {"format": "devbench.workbench-inputs/1", "inputs": rows, "truncated": truncated,
                "limits": dict(self.limits), "scope": "Read-only configured input discovery; file identity is not scientific validity."}

    def inspect(self, source):
        from devharness_core.watchdog import run_sandboxed, HarnessResourceInterrupt
        with self._lock:
            path, root, parts, identity = self._source(source)
            with _open_file(root, parts, identity) as descriptor:
                signature = _signature(os.fstat(descriptor))
                if signature[3] > self.limits["max_file_bytes"]:
                    raise ValueError("Input exceeds the configured inspection byte budget")
            cached = self._profiles.get(path)
            if cached and cached[0] == signature:
                _unchanged(root, parts, identity, signature)
                return json.loads(json.dumps(cached[1]))
            diagnostics = {}
            try:
                result = run_sandboxed(_inspect_worker, (str(root), parts, identity, signature, self.limits),
                    max_mem_mb=self.limits["memory_mb"], timeout_sec=self.limits["timeout_seconds"], diagnostics=diagnostics)
            except HarnessResourceInterrupt as error:
                raise RuntimeError("Input inspection exceeded its resource bounds: " + str(error)[:500]) from error
            if diagnostics.get("cleanup", {}).get("verified_stopped") is not True:
                raise RuntimeError("Input inspection worker cleanup is unverified; no profile was cached")
            _unchanged(root, parts, identity, signature)
            observed = result["observed"]
            columns = observed.get("obs_columns") or observed.get("columns") or []
            profile = {"format": "devbench.dataset-profile/1", "source": str(path), "path": str(path),
                "filename": path.name, "size_bytes": signature[3], "sha256": result["sha256"], "observed": observed,
                "declarations": {}, "inferred": {"candidate_columns": _candidates(columns),
                    "basis": "Column-name suggestions only; experimental identity is unconfirmed."},
                "unknowns": [{"field": name, "reason": reason, "blocking": blocking} for name, reason, blocking in [
                    ("assay", "File extension and matrix dimensions do not establish the assay or biological units.", True),
                    ("measurement", "No expression values were inspected; raw counts and normalization are unconfirmed.", True),
                    ("count_provenance", "The quantification protocol and count-layer provenance require experimental records.", True),
                    ("capture_unit_identity", "Column names do not establish capture units or independent donors, subjects or animals.", True),
                    ("annotation_provenance", "Annotation source, accuracy and biological validity were not assessed.", True),
                    ("design_and_contrasts", "Groups, contrasts, replication and confounding were not audited by header inspection.", False)]],
                "inspection": {"worker_cleanup_verified": True, "method": "trusted_supervised_read_only_headers",
                    "analysis_executed": False, "model_sessions_attempted": 0}}
            self._cache(self._hashes, path, signature, result["sha256"])
            self._cache(self._profiles, path, signature, profile)
            return json.loads(json.dumps(profile))
