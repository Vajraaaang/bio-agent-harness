"""Bounded reads for the registered supplied-population count input."""
from contextlib import contextmanager
import math
import os
from pathlib import Path

def _node(group, name):
    import h5py
    if (type(name) is not str or not name or "/" in name or "\x00" in name
            or name in {".", ".."}):
        raise ValueError("Selected HDF5 names must identify a direct child")
    if not isinstance(group.get(name, getlink=True), h5py.HardLink):
        raise ValueError("Selected HDF5 inputs must use existing internal hard links")
    node = group[name]
    if isinstance(node, h5py.Dataset) and (node.external or node.is_virtual):
        raise ValueError("Selected HDF5 datasets cannot use external or virtual storage")
    return node


def _validate_sparse_storage(source, block_bytes):
    """Validate every stored coordinate before AnnData can select sparse rows."""
    import h5py
    group = source._element
    if isinstance(group, h5py.Dataset):
        return
    _sparse_components(group, source.shape, source.matrix.format, block_bytes)


def _sparse_components(group, shape, format_name, block_bytes):
    import h5py
    import numpy as np

    data, indices, indptr = (_node(group, name) for name in ("data", "indices", "indptr"))
    major, minor = shape if format_name == "csr" else shape[::-1]
    if (any(not isinstance(value, h5py.Dataset) or len(value.shape) != 1 for value in (data, indices, indptr))
            or data.shape != indices.shape or indptr.shape != (major+1,)
            or indices.dtype.kind not in "iu" or indptr.dtype.kind not in "iu"):
        raise ValueError("Sparse count storage component lengths or dtypes are invalid")
    previous = None
    count = max(1, block_bytes // (4 * max(indices.dtype.itemsize, indptr.dtype.itemsize)))
    for start in range(0, len(indptr), count):
        pointer = indptr[start:start+count]
        if (np.any(pointer > len(data)) or np.any(pointer < 0)
                or np.any(pointer[1:] < pointer[:-1])
                or (previous is not None and pointer[0] < previous)
                or (start == 0 and pointer[0] != 0)):
            raise ValueError("Sparse count storage pointers are not monotone valid offsets")
        previous = int(pointer[-1])
    if previous != len(data):
        raise ValueError("Sparse count storage pointers do not cover every stored entry")
    for start in range(0, len(indices), count):
        coordinates = indices[start:start+count]
        if np.any(coordinates < 0) or np.any(coordinates >= minor):
            raise ValueError("Sparse count storage coordinates exceed the declared axis")
    return data, indices, indptr


def _annotation(element, expected, budget):
    """Read one bounded AnnData annotation after checking all its child links."""
    import anndata as ad
    import h5py
    import numpy as np
    total = 0
    def check(node, depth=0):
        nonlocal total
        if depth > 3:
            raise ValueError("Annotation encoding exceeds supported depth")
        if isinstance(node, h5py.Dataset):
            if len(node.shape) != 1 or node.shape[0] > max(expected, 1):
                raise ValueError("Annotation shape exceeds metadata bounds")
            total += int(node.size) * max(node.dtype.itemsize, 256 if node.dtype.kind == "O" else 1)
            if total > budget:
                raise ValueError("Annotation exceeds metadata byte budget")
        else:
            names = list(node.keys())
            if len(names) > 4:
                raise ValueError("Unsupported annotation encoding")
            for name in names:
                check(_node(node, name), depth+1)
    check(element)
    values = ad.io.read_elem(element)
    if len(values) != expected:
        raise ValueError("Annotation axis does not match selected matrix")
    actual_bytes = 0
    for value in values:
        if value is not None and not (isinstance(value, float) and math.isnan(value)):
            if not isinstance(value, (str, np.str_)):
                raise ValueError("Declared annotation columns and identifiers must contain textual labels")
            if not value.strip() or len(value) > 200 or "\x00" in value:
                raise ValueError("Annotation label exceeds metadata bounds")
            actual_bytes += len(value.encode("utf-8"))
    if actual_bytes > budget:
        raise ValueError("Annotation exceeds decoded metadata byte budget")
    return values, max(total, actual_bytes)


def _open_selected(handle, specification):
    import anndata as ad
    import h5py
    import pandas as pd
    from .expression_io import BackedExpression
    layer = _node(_node(handle, "layers"), specification["counts_layer"])
    if isinstance(layer, h5py.Dataset):
        shape = layer.shape
        matrix = layer
    else:
        shape = tuple(layer.attrs.get("shape", ()))
        for key in ("data", "indices", "indptr"):
            _node(layer, key)
        matrix = ad.io.sparse_dataset(layer)
    limits = specification["limits"]
    if len(shape) != 2 or min(shape) < 1 or shape[0] > limits["max_cells"] or shape[1] > limits["max_features"]:
        raise ValueError("Selected matrix cell/feature dimensions exceed the complete-family bounds")
    cells, features = map(int, shape)
    obs_group, var_group = _node(handle, "obs"), _node(handle, "var")
    obs_index, var_index = obs_group.attrs.get("_index", "_index"), var_group.attrs.get("_index", "_index")
    columns = list(dict.fromkeys([specification["biological_unit_column"], specification["stage_column"],
        *specification["technical_alias_columns"], *(p["column"] for p in specification["populations"])]))
    remaining = limits["max_metadata_bytes"]
    observed_ids, used = _annotation(_node(obs_group, obs_index), cells, remaining)
    remaining -= used
    feature_ids, used = _annotation(_node(var_group, var_index), features, remaining)
    remaining -= used
    if any(value is None or not isinstance(value, str) for value in list(observed_ids) + list(feature_ids)):
        raise ValueError("Observation and feature IDs must be nonmissing strings")
    if len(set(observed_ids)) != cells or len(set(feature_ids)) != features:
        raise ValueError("Observation and feature identifiers must be unique")
    obs = pd.DataFrame(index=observed_ids)
    for column in columns:
        values, used = _annotation(_node(obs_group, column), cells, remaining)
        remaining -= used
        obs[column] = values
    # Reuse the existing bounded CSR/dense row reader with only the explicitly
    # needed annotations. Avoid deserializing unrelated layers, raw, uns or obs.
    class SelectedExpression(BackedExpression):
        def __init__(self):
            self._handle, self._element, self.matrix = handle, layer, matrix
            self.matrix_source = "layers/" + specification["counts_layer"]
            self.shape, self.obs = (cells, features), obs
            self.var = pd.DataFrame(index=feature_ids)
    return SelectedExpression()


@contextmanager
def _source(path, specification):
    import h5py
    from .input_files import _hash_fd, _open_file, _signature
    supplied = Path(path)
    if not supplied.is_absolute() or supplied.suffix.lower() != ".h5ad" or supplied.resolve() != supplied:
        raise ValueError("Supplied population input requires a canonical absolute H5AD path")
    root = supplied.parent
    info = root.stat()
    with _open_file(root, (supplied.name,), (info.st_dev, info.st_ino)) as descriptor:
        before = _signature(os.fstat(descriptor))
        if _hash_fd(descriptor, specification["limits"]["max_file_bytes"]) != specification["source_sha256"]:
            raise ValueError("source_sha256 does not match the supplied immutable input")
        os.lseek(descriptor, 0, os.SEEK_SET)
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            with h5py.File(stream, "r") as handle:
                yield _open_selected(handle, specification)
        if _signature(os.fstat(descriptor)) != before or _hash_fd(descriptor, specification["limits"]["max_file_bytes"]) != specification["source_sha256"]:
            raise ValueError("source_sha256 changed during population analysis")
    after = supplied.stat()
    if _signature(after) != before:
        raise ValueError("Source path identity changed during population analysis")


@contextmanager
def bound_query_file(path, sha256, maximum):
    """Read through the same regular-file descriptor that was hash checked."""
    from .input_files import _hash_fd, _open_file, _signature

    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("Evidence input requires a canonical absolute path")
    info = path.parent.stat()
    with _open_file(path.parent, (path.name,), (info.st_dev, info.st_ino)) as descriptor:
        signature = _signature(os.fstat(descriptor))
        if _hash_fd(descriptor, maximum) != sha256:
            raise ValueError("Evidence input hash mismatch")
        os.lseek(descriptor, 0, os.SEEK_SET)
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            yield stream
        if (_signature(os.fstat(descriptor)) != signature
                or _signature(path.stat()) != signature):
            raise ValueError("Evidence input changed during query")


def _query_axis(bindings, name, *, unique=True):
    ids = bindings.get(name)
    if (not isinstance(ids, (list, tuple)) or not ids or len(ids) > 100_000
            or any(not isinstance(value, str) or not value.strip() or len(value) > 200 for value in ids)
            or (unique and len(ids) != len(set(ids)))):
        raise ValueError("Evidence query requires an exact " + name + " axis")
    return list(ids)


def query_population_counts(bindings, wanted, selected_samples, maximum):
    """Return only the requested feature/observation intersection and library totals."""
    features = _query_axis(bindings, "gene_ids")
    samples = _query_axis(bindings, "sample_ids")
    units = _query_axis(bindings, "unit_ids", unique=False)
    if len(samples) != len(units) or set(selected_samples) - set(samples):
        raise ValueError("Evidence query sample/unit axis mismatch")
    positions = {feature: index for index, feature in enumerate(features) if feature in wanted}
    with bound_query_file(bindings["counts_path"], bindings.get("counts_sha256"), maximum) as stream:
        if bindings["counts_transport"] == "h5ad_csr":
            counts, totals = _query_csr(stream, bindings, features, samples, units, positions,
                                        selected_samples, maximum)
        else:
            counts, totals = _query_profiles(stream, features, samples, units, positions, selected_samples, maximum)
    return counts, totals, set(features), dict(zip(samples, units, strict=True))


def _query_csr(stream, bindings, features, samples, units, positions, selected_samples, maximum):
    import h5py
    import numpy as np

    block_bytes = 1024 * 1024
    with h5py.File(stream, "r") as handle:
        location = bindings.get("counts_matrix")
        if location == "X":
            matrix = _node(handle, "X")
        elif isinstance(location, str) and location.startswith("layers/"):
            matrix = _node(_node(handle, "layers"), location.removeprefix("layers/"))
        elif location == "raw/X":
            matrix = _node(_node(handle, "raw"), "X")
        else:
            raise ValueError("A specific admitted H5AD CSR matrix is required")
        shape = (len(samples), len(features))
        if (not isinstance(matrix, h5py.Group) or matrix.attrs.get("encoding-type") != "csr_matrix"
                or tuple(matrix.attrs.get("shape", ())) != shape):
            raise ValueError("Admitted H5AD input requires CSR storage with the exact bound axis")
        components = [_node(matrix, name) for name in ("data", "indices", "indptr")]
        if (any(not isinstance(item, h5py.Dataset) or item.dtype.kind not in "iuf"
                or (item.chunks and math.prod(item.chunks) * item.dtype.itemsize > block_bytes)
                for item in components)
                or sum(item.size * item.dtype.itemsize for item in components) > maximum):
            raise ValueError("CSR decoded storage exceeds the read bound or has unsupported dtype")
        remaining = min(maximum - sum(item.size * item.dtype.itemsize for item in components), 8_000_000)
        obs = _node(handle, "obs")
        var = _node(_node(handle, "raw"), "var") if location == "raw/X" else _node(handle, "var")
        for group, name, expected in ((obs, obs.attrs.get("_index", "_index"), samples),
                                      (var, var.attrs.get("_index", "_index"), features),
                                      (obs, bindings.get("unit_id_column"), units)):
            actual, used = _annotation(_node(group, name), len(expected), remaining)
            if list(actual) != expected:
                raise ValueError("H5AD feature/sample/unit axis mismatch")
            remaining -= used
        data, indices, pointers = _sparse_components(matrix, shape, "csr", block_bytes)
        values = {gene: {} for gene in positions}
        totals = {}
        wanted_indices = {index: gene for gene, index in positions.items()}
        for row, sample in enumerate(samples):
            if sample not in selected_samples:
                continue
            start, stop = map(int, pointers[row:row+2])
            seen = set()
            total = 0
            for gene in positions:
                values[gene][sample] = 0
            for left in range(start, stop, block_bytes // 32):
                right = min(stop, left + block_bytes // 32)
                counts = data[left:right]
                if (not np.all(np.isfinite(counts)) or np.any(counts < 0)
                        or np.any(counts >= 2**53) or np.any(counts != np.floor(counts))):
                    raise ValueError("CSR contains invalid raw counts")
                for index, count in zip(indices[left:right].tolist(), counts.tolist(), strict=True):
                    if index in seen:
                        raise ValueError("CSR contains duplicate feature coordinates")
                    seen.add(index)
                    total += int(count)
                    if index in wanted_indices:
                        values[wanted_indices[index]][sample] = int(count)
            totals[sample] = total
        return values, totals


def _query_profiles(stream, features, samples, units, positions, selected_samples, maximum):
    """Decode one count-profile row at a time from the bounded JSON artifact."""
    import json
    import re

    raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("Persisted count profiles exceed the read bound")
    text = raw.decode("utf-8")
    def unique_fields(pairs):
        fields = dict(pairs)
        if len(fields) != len(pairs):
            raise ValueError("Duplicate persisted count-profile field")
        return fields

    decoder = json.JSONDecoder(object_pairs_hook=unique_fields)
    whitespace = re.compile(r"\s*")
    index = 0

    def at():
        return whitespace.match(text, index).end()

    def consume(token):
        nonlocal index
        index = at()
        if not text.startswith(token, index):
            raise ValueError("Corrupt persisted count profiles")
        index += len(token)

    def decode():
        nonlocal index
        value, index = decoder.raw_decode(text, at())
        return value

    values = {gene: {} for gene in positions}
    totals, metadata, seen_keys = {}, {}, set()
    observed_samples, observed_units = [], []
    consume("{")
    while not text.startswith("}", at()):
        key = decode()
        if not isinstance(key, str) or key in seen_keys:
            raise ValueError("Duplicate or invalid persisted count-profile field")
        seen_keys.add(key)
        consume(":")
        if key == "profiles":
            consume("[")
            while not text.startswith("]", at()):
                profile = decode()
                if not isinstance(profile, dict) or not isinstance(profile.get("key"), dict):
                    raise ValueError("Invalid persisted profile or biological key")
                sample = profile.get("profile_id")
                observed_samples.append(sample)
                observed_units.append(profile["key"].get("unit_id"))
                row = len(observed_samples) - 1
                if row >= len(samples) or sample != samples[row] or observed_units[row] != units[row]:
                    raise ValueError("Persisted count profile axis mismatch")
                if not {"counts", "status", "library_size"} <= profile.keys():
                    raise ValueError("Persisted profile is missing its count or status binding")
                counts = profile["counts"]
                status = profile["status"]
                library_size = profile["library_size"]
                if status in {"observed", "observed_zero"}:
                    if (not isinstance(counts, list) or len(counts) != len(features)
                            or any(type(count) is not int or not 0 <= count < 2**53 for count in counts)):
                        raise ValueError("Persisted profile contains invalid raw counts")
                    total = sum(counts)
                    if (status == "observed_zero") != (total == 0) or total != library_size:
                        raise ValueError("Persisted profile library size or zero status is inconsistent")
                elif status == "absent" and counts is None and library_size is None:
                    total = None
                elif (status == "excluded" and counts is None
                      and (library_size is None or type(library_size) is int and 0 <= library_size < 2**53)):
                    # The measured library size is retained for audit. Excluded
                    # counts and their totals remain unavailable to queries.
                    total = None
                else:
                    raise ValueError("Persisted profile status and count data disagree")
                if sample in selected_samples:
                    totals[sample] = total
                    for gene, position in positions.items():
                        values[gene][sample] = None if total is None else counts[position]
                if text.startswith("]", at()):
                    break
                consume(",")
                if text.startswith("]", at()):
                    raise ValueError("Corrupt persisted count profiles")
            consume("]")
        elif key in {"format", "measurement", "feature_ids", "reference", "components"}:
            metadata[key] = decode()
        else:
            raise ValueError("Unsupported persisted count-profile field")
        if text.startswith("}", at()):
            break
        consume(",")
        if text.startswith("}", at()):
            raise ValueError("Corrupt persisted count profiles")
    consume("}")
    if (at() != len(text) or metadata.get("format") != "devbench.count-profiles/1"
            or metadata.get("measurement") not in {"umi_counts", "non_umi_counts"}
            or metadata.get("feature_ids") != features or observed_samples != samples or observed_units != units):
        raise ValueError("Persisted count profile schema or feature/sample/unit axis mismatch")
    return values, totals
