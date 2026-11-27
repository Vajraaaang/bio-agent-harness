"""Read local AnnData Zarr v2/v3 arrays through the project's pinned Zarr runtime."""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
import zarr

from ._files import read_bounded_file

_CHUNK_BYTES = 64 * 1024**2
_METADATA_BYTES = 16 * 1024**2
_SAFE_CODECS = {
    "blosc",
    "zstd",
    "zlib",
    "gzip",
    "bz2",
    "lz4",
    "lzma",
    "delta",
    "shuffle",
    "vlen-utf8",
    "vlen-bytes",
    "crc32",
    "adler32",
    "fixedscaleoffset",
    "packbits",
}


def _v3_compressor(name, config):
    if name == "blosc":
        config.pop("typesize", None)
        shuffle = config.get("shuffle", "shuffle")
        if isinstance(shuffle, str):
            config["shuffle"] = {"noshuffle": 0, "shuffle": 1, "bitshuffle": 2}[shuffle]
    if name not in {"gzip", "zstd", "blosc"}:
        raise ValueError(f"Unsupported Zarr v3 chunk codec: {name}")
    return {"id": name, **config}


def _codecs(codecs, dtype, ndim):
    filters, compressor, order = [], None, "C"
    for codec in codecs:
        name, config = codec["name"], dict(codec.get("configuration", {}))
        if name == "bytes":
            dtype = dtype.newbyteorder(
                {"little": "<", "big": ">"}[config.get("endian", "little")]
            )
        elif name in {"vlen-utf8", "vlen-bytes"}:
            filters.append({"id": name})
        elif name == "transpose":
            axes = config["order"]
            if axes == list(reversed(range(ndim))):
                order = "F"
            elif axes != list(range(ndim)):
                raise ValueError("Unsupported Zarr v3 chunk transpose")
        elif compressor is None:
            compressor = _v3_compressor(name, config)
        else:
            raise ValueError("Unsupported stacked Zarr v3 compression codecs")
    if dtype.kind == "O" and not filters:
        raise ValueError("Zarr object arrays require a safe string codec")
    return dtype, filters, compressor, order


def _array_metadata(meta):
    if meta["chunk_grid"]["name"] != "regular" or meta.get("storage_transformers"):
        raise ValueError("Zarr expression requires a regular, local chunk grid")
    chunks = meta["chunk_grid"]["configuration"]["chunk_shape"]
    dtype_name = meta["data_type"]
    if isinstance(dtype_name, dict) and dtype_name["name"] == "fixed_length_utf32":
        dtype = np.dtype("U" + str(dtype_name["configuration"]["length_bytes"] // 4))
    else:
        dtype = np.dtype("O" if dtype_name in ("string", "bytes") else dtype_name)
    dtype, filters, compressor, order = _codecs(meta["codecs"], dtype, len(chunks))
    return {
        "zarr_format": 2,
        "shape": meta["shape"],
        "chunks": chunks,
        "dtype": dtype.str,
        "compressor": compressor,
        "filters": filters or None,
        "order": order,
        "fill_value": meta.get("fill_value"),
        "dimension_separator": ".",
    }


def _check_array(meta):
    codecs = [meta.get("compressor"), *(meta.get("filters") or ())]
    if any(codec and codec.get("id") not in _SAFE_CODECS for codec in codecs):
        raise ValueError("Untrusted Zarr codec in expression store")
    if math.prod(meta["chunks"]) * np.dtype(meta["dtype"]).itemsize > _CHUNK_BYTES:
        raise ValueError("Zarr physical chunk exceeds the expression read budget")


class _ExpressionStore(zarr.storage.DirectoryStore):
    """Translate v3 keys/metadata lazily; numcodecs decodes only requested chunks."""

    def __init__(self, path):
        super().__init__(str(path))
        self.root = path
        self.v3 = (path / "zarr.json").is_file()
        self._metadata = lru_cache(maxsize=128)(self._read_metadata)

    def _read_metadata(self, path):
        return json.loads(read_bounded_file(path / "zarr.json", _METADATA_BYTES))

    def _translate(self, key):
        full = self.root / key
        if full.is_absolute() and not full.is_relative_to(self.root):
            raise KeyError(key)
        if ".." in Path(key).parts or any(
            p.is_symlink() for p in (full, *full.parents) if p.is_relative_to(self.root)
        ):
            raise ValueError("Zarr keys must resolve inside the admitted local store")
        if not self.v3:
            return full, None
        parent, filename = full.parent, full.name
        meta = self._metadata(parent)
        if filename == ".zattrs":
            return None, meta.get("attributes", {})
        if filename == ".zgroup" and meta["node_type"] == "group":
            return None, {"zarr_format": 2}
        if filename == ".zarray" and meta["node_type"] == "array":
            return None, _array_metadata(meta)
        if filename.startswith(".") or meta["node_type"] != "array":
            raise KeyError(key)
        encoding = meta["chunk_key_encoding"]
        name = encoding["name"]
        if name not in {"default", "v2"}:
            raise ValueError("Unsupported Zarr chunk key encoding")
        separator = encoding.get("configuration", {}).get(
            "separator", "/" if name == "default" else "."
        )
        if separator not in {".", "/"} or any(
            not n.isdecimal() for n in filename.split(".")
        ):
            raise ValueError("Invalid Zarr chunk key")
        coordinates = filename.split(".") if meta["shape"] else []
        parts = ["c", *coordinates] if name == "default" else (coordinates or ["0"])
        return parent / separator.join(parts), None

    def __getitem__(self, key):
        try:
            path, metadata = self._translate(key)
            if metadata is not None:
                if key.endswith(".zarray"):
                    _check_array(metadata)
                return json.dumps(metadata).encode("utf8")
            data = read_bounded_file(
                path,
                _METADATA_BYTES
                if Path(key).name.startswith(".")
                else _CHUNK_BYTES + 65536,
            )
            if key.endswith(".zarray"):
                _check_array(json.loads(data))
            return data
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError) as error:
            raise KeyError(key) from error

    def __contains__(self, key):
        try:
            path, metadata = self._translate(key)
            return metadata is not None or path.is_file()
        except (KeyError, FileNotFoundError, NotADirectoryError):
            return False

    def listdir(self, path=None):
        children = super().listdir(path)
        if self.v3 and "zarr.json" in children:
            kind = self._metadata(self.root / (path or ""))["node_type"]
            return [name for name in children if name != "zarr.json"] + [
                ".zattrs",
                ".zgroup" if kind == "group" else ".zarray",
            ]
        return children


def open_zarr_group(path):
    return zarr.open_group(store=_ExpressionStore(path), mode="r")
