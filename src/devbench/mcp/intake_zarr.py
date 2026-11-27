"""Project checked Zarr v3 metadata into the pinned v2 array reader."""

from copy import deepcopy
from pathlib import PurePosixPath
from types import SimpleNamespace

from devbench.core.zarr_expression_store import _array_metadata


def _chunk_name(array, tail):
    encoding = array["chunk_key_encoding"]
    name = encoding["name"]
    separator = encoding.get("configuration", {}).get(
        "separator", "/" if name == "default" else "."
    )
    if name not in {"default", "v2"} or separator not in {".", "/"}:
        raise ValueError("Unsupported Zarr chunk key encoding")
    coords = tail.split(separator)
    if name == "default" and coords.pop(0) != "c":
        return None
    if len(coords) != len(array["shape"]) or not all(c.isdecimal() for c in coords):
        return None
    return ".".join(coords) or "0"


def inspected_store(tree):
    if "zarr.json" not in tree.metadata:
        return tree
    metadata, arrays, chunks, paths = {}, {}, {}, {}
    for key, item in tree.metadata.items():
        if PurePosixPath(key).name != "zarr.json":
            continue
        prefix = key.removesuffix("zarr.json")
        metadata[prefix + ".zattrs"] = item.get("attributes", {})
        if item.get("node_type") == "group":
            metadata[prefix + ".zgroup"] = {"zarr_format": 2}
        elif prefix.split("/")[0] in {"obs", "var", "X"}:
            metadata[prefix + ".zarray"] = _array_metadata(deepcopy(item))
            arrays[prefix.rstrip("/")] = item
    for key, digest in tree.array_chunks.items():
        for parent in PurePosixPath(key).parents:
            axis = str(parent)
            if axis in arrays:
                suffix = _chunk_name(arrays[axis], key[len(axis) + 1 :])
                if suffix is not None:
                    virtual = axis + "/" + suffix
                    paths[virtual], chunks[virtual] = key, digest
                break
    return SimpleNamespace(
        path=tree.path,
        until=tree.until,
        sha256=tree.sha256,
        metadata=metadata,
        array_chunks=chunks,
        chunk_paths=paths,
    )
