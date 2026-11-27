"""Local storage and slot inventory. Inspecting a matrix never grants COUNT-1."""

from __future__ import annotations

import json
import os
from itertools import islice
from pathlib import Path

import h5py
import numpy as np

from ._files import read_bounded_file
from .input_files import _open_file
from .population_data import _node

_METADATA_BYTES = 4 << 20
_MAX_SLOTS = 1024


def _names(group):
    names = list(islice(iter(group), _MAX_SLOTS + 1))
    if len(names) > _MAX_SLOTS:
        raise ValueError("Expression slot inventory exceeds its metadata budget")
    return sorted(names)


def _slots(group, name):
    return _names(_node(group, name)) if name in group else []


def _h5_matrix(group):
    x = _node(group, "X")
    shape = x.shape if isinstance(x, h5py.Dataset) else x.attrs["shape"]
    return {
        "shape": tuple(int(axis) for axis in shape),
        "encoding": x.attrs.get("encoding-type", "array"),
        "layers": _slots(group, "layers"),
        "obsm": _slots(group, "obsm"),
        "obs_columns": _slots(group, "obs"),
        "var_columns": _slots(group, "var"),
        "count_1_verified": False,
    }


def _member(group, name):
    if not isinstance(name, str) or not name or "/" in name or name in {".", ".."}:
        raise ValueError("Invalid local slot name")
    return _node(group, name) if isinstance(group, h5py.Group) else group[name]


def _index(group):
    obs = _member(group, "obs")
    return _member(obs, obs.attrs.get("_index", "_index"))


def _aligned(root, modality, name):
    master, local = _index(root), _index(modality)
    mapping = _member(_member(root, "obsmap"), name) if "obsmap" in root else None
    if isinstance(root, h5py.Group):
        from .hdf5_custody import validate_hdf5_tree

        for dataset in (master, local, mapping):
            if dataset is not None:
                validate_hdf5_tree(dataset)
    if mapping is None and master.shape != local.shape:
        return False
    if mapping is not None and (
        mapping.shape not in {(len(master),), (len(master), 1)}
        or mapping.dtype.kind not in "iu"
    ):
        return False
    if mapping is not None:
        return _aligned_map(master, local, mapping)
    for start in range(0, len(master), 1024):
        if not np.array_equal(
            master[start : start + 1024], local[start : start + 1024]
        ):
            return False
    return True


def _aligned_map(master, local, mapping):
    seen = np.zeros((len(local) + 7) // 8, dtype=np.uint8)
    matched = 0
    for start in range(0, len(master), 1024):
        block = mapping[start : start + 1024].reshape(-1)
        if np.any(block < 0) or np.any(block > len(local)):
            return False
        present = block > 0
        ids, inverse = np.unique(
            block[present].astype(np.int64) - 1, return_inverse=True
        )
        flags = np.left_shift(np.uint8(1), (ids % 8).astype(np.uint8))
        if len(ids) != int(present.sum()) or np.any(seen[ids // 8] & flags):
            return False
        np.bitwise_or.at(seen, ids // 8, flags)
        if len(ids) and not np.array_equal(
            master[start : start + 1024][present], local[ids][inverse]
        ):
            return False
        matched += len(ids)
    return matched == len(local)


def _hdf5(path):
    parent = path.parent.stat()
    with _open_file(path.parent, (path.name,), (parent.st_dev, parent.st_ino)) as fd:
        with os.fdopen(os.dup(fd), "rb") as stream, h5py.File(stream, "r") as root:
            if "mod" not in root:
                return {"format": "h5ad", **_h5_matrix(root)}
            mods = _node(root, "mod")
            inventory = {}
            for name in _names(mods):
                group = _node(mods, name)
                inventory[name] = {
                    **_h5_matrix(group),
                    "obs_aligned": _aligned(root, group, name),
                }
            return {"format": "h5mu", "modalities": inventory, "layers": []}


def _metadata(path):
    return json.loads(read_bounded_file(path, _METADATA_BYTES))


def _zarr_attributes(path):
    if (path / "zarr.json").is_file():
        return _metadata(path / "zarr.json").get("attributes", {})
    if (path / ".zattrs").is_file():
        return _metadata(path / ".zattrs")
    return {}


def _children(path):
    if path.is_symlink():
        raise ValueError("Zarr groups must be local directories")
    if not path.is_dir():
        return []
    with os.scandir(path) as entries:
        return _names(
            entry.name for entry in entries if entry.is_dir(follow_symlinks=False)
        )


def _zarr_matrix(path):
    x = path / "X"
    meta = x / "zarr.json" if (x / "zarr.json").is_file() else x / ".zarray"
    attributes = _zarr_attributes(x)
    shape = attributes.get("shape")
    if shape is None:
        shape = _metadata(meta)["shape"]
    return {
        "shape": tuple(int(axis) for axis in shape),
        "encoding": attributes.get("encoding-type", "array"),
        "layers": _children(path / "layers"),
        "obsm": _children(path / "obsm"),
        "obs_columns": _children(path / "obs"),
        "var_columns": _children(path / "var"),
        "count_1_verified": False,
    }


def _zarr_store(path):
    version = 3 if (path / "zarr.json").is_file() else 2
    marker = "zarr.json" if version == 3 else ".zgroup"
    if _metadata(path / marker)["zarr_format"] != version:
        raise ValueError("Invalid root Zarr metadata")
    if (path / "mod").is_dir():
        from .zarr_expression_store import open_zarr_group

        root = open_zarr_group(path)
        return {
            "format": "mudata_zarr",
            "zarr_format": version,
            "modalities": {
                n: {
                    **_zarr_matrix(path / "mod" / n),
                    "obs_aligned": _aligned(root, root["mod"][n], n),
                }
                for n in _children(path / "mod")
            },
        }
    spatial = {
        key: _children(path / key)
        for key in ("images", "labels", "points", "shapes", "tables")
    }
    if any(spatial.values()) or "spatialdata_attrs" in _zarr_attributes(path):
        return {
            "format": "spatialdata",
            "zarr_format": version,
            "elements": spatial,
            "coordinate_metadata": {
                f"{kind}/{name}": {
                    key: value
                    for key, value in _zarr_attributes(path / kind / name).items()
                    if key
                    in {"multiscales", "coordinateTransformations", "coordinateSystems"}
                }
                for kind, names in spatial.items()
                for name in names
            },
        }
    return {"format": "zarr", "zarr_format": version, **_zarr_matrix(path)}


def _soma(path):
    if not any((path / key).exists() for key in ("__tiledb_group.tdb", "__group")):
        raise ValueError("TileDB group metadata is missing")
    try:
        import tiledbsoma as soma
    except ImportError:
        return {"format": "tiledb_soma", "reader_available": False, "slots": None}
    with soma.open(str(path), mode="r") as obj:
        return {
            "format": "tiledb_soma",
            "reader_available": True,
            "object_type": obj.soma_type,
            "slots": _names(obj.keys()),
        }


def expression_inventory(path: str | Path) -> dict:
    """Inspect selected local container metadata without loading expression arrays."""
    path = Path(path)
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError("Expression inventory requires a canonical local path")
    if path.is_file():
        return _hdf5(path)
    if path.suffix == ".soma" or (path / "__tiledb_group.tdb").exists():
        return _soma(path)
    return _zarr_store(path)
