"""Bound prepared Zarr-v2 frames before the numerical runtime decodes them.

Call only inside an authorized data operation on a verified coordinator-owned
snapshot. This module has no numerical/codec-runtime imports. It accepts the
three fixed arrays used by ChunkedExpressionStore, complete chunk coverage,
empty attributes and no filters. Concurrent hostile filesystem replacement is
outside the coordinator-owned snapshot boundary.
"""

from __future__ import annotations

from itertools import product
import json
import math
from pathlib import Path, PurePosixPath
import re
import struct
from typing import Any
import zlib

from .biology_input_limits import PUBLIC_MAX_BULK_FEATURES


ARRAYS = ("expression", "gene_names", "sample_names")
MAX_FILES = 20_000
MAX_GENES = 5000
DEFAULT_CHUNK_BYTES = 4 * 1024 * 1024
_INDEX = r"(?:0|[1-9][0-9]*)"


def is_prepared_zarr_member(name: str) -> bool:
    """Pure exact member-name contract; no arbitrary attribute/reference sidecars."""
    if not isinstance(name, str) or len(name) > 200 or PurePosixPath(name).as_posix() != name:
        return False
    if name in {".zgroup", ".zattrs"}:
        return True
    parts = name.split("/")
    if len(parts) != 2 or parts[0] not in ARRAYS:
        return False
    if parts[1] in {".zarray", ".zattrs"}:
        return True
    pattern = _INDEX + r"\." + _INDEX if parts[0] == "expression" else _INDEX
    return re.fullmatch(pattern, parts[1]) is not None


def _json_object(content: bytes) -> dict[str, Any]:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Repeated metadata JSON keys are not permitted")
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError("Nonfinite metadata values are not permitted")

    value = json.loads(content, object_pairs_hook=pairs, parse_constant=nonfinite)
    if not isinstance(value, dict):
        raise ValueError("Zarr metadata must be an object")
    return value


def validate_empty_attributes(content: bytes) -> dict[str, Any]:
    value = _json_object(content)
    if value:
        raise ValueError("Prepared Zarr attributes must be empty; arbitrary provenance/sidecars are not public inputs")
    return value


def _compressor_config(compressor: dict[str, Any] | None) -> str | None:
    if compressor is None:
        return None
    if not isinstance(compressor, dict):
        raise ValueError("Unsupported compressor declaration")
    codec = compressor.get("id")
    if codec in {"zlib", "gzip"}:
        if set(compressor) - {"id", "level"} or type(compressor.get("level", 1)) is not int or not 0 <= compressor.get("level", 1) <= 9:
            raise ValueError("Unsupported gzip/zlib compressor configuration")
    elif codec == "blosc":
        if set(compressor) - {"id", "cname", "clevel", "shuffle", "blocksize"}:
            raise ValueError("Unsupported Blosc compressor configuration")
        if compressor.get("cname", "lz4") not in {"blosclz", "lz4", "lz4hc", "snappy", "zlib", "zstd"}:
            raise ValueError("Unsupported Blosc compression library")
        for key, default, low, high in (("clevel", 5, 0, 9), ("shuffle", 1, -1, 2), ("blocksize", 0, 0, DEFAULT_CHUNK_BYTES)):
            value = compressor.get(key, default)
            if type(value) is not int or not low <= value <= high:
                raise ValueError("Unsupported Blosc compressor parameter")
    else:
        raise ValueError("Only no codec, gzip, zlib and Blosc are supported")
    return codec


def validate_chunk_frame(payload: bytes, compressor: dict[str, Any] | None, expected_bytes: int,
                         *, max_chunk_bytes: int = DEFAULT_CHUNK_BYTES) -> int:
    """Validate actual frame allocation/output bounds without numerical arrays.

gzip/zlib are streamed with at most the remaining declared bytes plus one byte
of output; flush is never called. Blosc's checked 16-byte header is the source
of numcodecs 0.15.1's output allocation, not the Zarr shape declaration:
https://github.com/zarr-developers/numcodecs/blob/v0.15.1/numcodecs/blosc.pyx
https://github.com/Blosc/c-blosc/blob/main/blosc/blosc.c#L1970
Its native decoder subsequently validates compressed blocks within these bounds.
"""
    if (type(expected_bytes) is not int or type(max_chunk_bytes) is not int
            or not 1 <= expected_bytes <= max_chunk_bytes <= DEFAULT_CHUNK_BYTES):
        raise ValueError("Declared decoded chunk size exceeds its hard bound")
    if not isinstance(payload, bytes) or len(payload) > max_chunk_bytes + 65_536:
        raise ValueError("Encoded chunk exceeds its bounded frame size")
    codec = _compressor_config(compressor)
    if codec is None:
        if len(payload) != expected_bytes:
            raise ValueError("Uncompressed chunk buffer size differs from its declaration")
    elif codec == "blosc":
        if len(payload) < 16:
            raise ValueError("Truncated Blosc frame header")
        version, versionlz, flags, typesize, nbytes, blocksize, cbytes = struct.unpack_from("<BBBBIII", payload)
        if (version != 2 or versionlz != 1 or not 1 <= typesize <= 255 or flags >> 5 > 4
                or flags & 5 == 5 or nbytes != expected_bytes or cbytes != len(payload)
                or cbytes > nbytes + 16 or not 1 <= blocksize <= nbytes):
            raise ValueError("Blosc header sizes/version exceed the declared chunk or actual frame bounds")
        if flags & 2:
            if cbytes != nbytes + 16:
                raise ValueError("Blosc memcpy payload must match its exact declared size")
        else:
            blocks = (nbytes + blocksize - 1) // blocksize
            table_end = 16 + 4 * blocks
            if table_end >= cbytes:
                raise ValueError("Blosc block index exceeds the actual frame")
            offsets = struct.unpack_from("<" + "I" * blocks, payload, 16)
            if len(set(offsets)) != len(offsets) or any(offset < table_end or offset >= cbytes for offset in offsets):
                raise ValueError("Blosc block offsets exceed the actual frame")
    else:
        decoder = zlib.decompressobj(31 if codec == "gzip" else 15)
        decoded = 0
        cursor = 0
        pending = b""
        try:
            while cursor < len(payload) or pending:
                if not pending:
                    pending = payload[cursor:cursor + 65_536]
                    cursor += len(pending)
                output = decoder.decompress(pending, expected_bytes - decoded + 1)
                decoded += len(output)
                if decoded > expected_bytes:
                    raise ValueError("Actual decoded bytes exceed the declared chunk bound")
                pending = decoder.unconsumed_tail
                if decoder.eof:
                    if decoder.unused_data or pending or cursor != len(payload):
                        raise ValueError("Trailing or concatenated compressed streams are not permitted")
                    break
            if not decoder.eof or pending or decoded != expected_bytes:
                raise ValueError("Compressed stream is unfinished or has the wrong decoded size")
        except zlib.error as exc:
            raise ValueError("Malformed bounded compressed stream") from exc
    return expected_bytes


def _read_bounded(path: Path, maximum: int) -> bytes:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
        raise ValueError("Prepared Zarr member is not a bounded regular nonsymlink file")
    with path.open("rb") as handle:
        content = handle.read(maximum + 1)
    if len(content) > maximum:
        raise ValueError("Prepared Zarr member exceeds its read bound")
    return content


def _array_header(header: dict[str, Any], name: str, *, max_chunk_bytes: int, max_array_bytes: int) -> dict[str, Any]:
    required = {"zarr_format", "shape", "chunks", "dtype", "compressor", "fill_value", "order", "filters"}
    if not required <= set(header) or set(header) - required - {"dimension_separator"}:
        raise ValueError("Unsupported or incomplete prepared Zarr array metadata")
    if (type(header["zarr_format"]) is not int or header["zarr_format"] != 2 or header["order"] != "C"
            or header["filters"] not in (None, []) or header.get("dimension_separator", ".") != "."):
        raise ValueError("Only filter-free Zarr v2 C-order complete chunks are supported")
    rank = 2 if name == "expression" else 1
    shape, chunks = header["shape"], header["chunks"]
    if (not isinstance(shape, list) or not isinstance(chunks, list) or len(shape) != rank or len(chunks) != rank
            or any(type(value) is not int or value < 1 for value in shape + chunks)):
        raise ValueError("Array shape/chunks must have fixed positive integer dimensions")
    pattern = r"[<>=|]([iuf])([1248])" if name == "expression" else r"[<>=|](U)([0-9]+)"
    match = re.fullmatch(pattern, header["dtype"]) if isinstance(header["dtype"], str) else None
    if match is None:
        raise ValueError("Only fixed numeric expression and fixed Unicode label dtypes are supported")
    kind, width = match.group(1), int(match.group(2))
    if kind == "f" and width == 1:
        raise ValueError("One-byte floating-point storage is unsupported")
    if kind == "U" and not 1 <= width <= 200:
        raise ValueError("Label character width exceeds the metadata bound")
    item_bytes = width * 4 if kind == "U" else width
    array_bytes, chunk_bytes = math.prod(shape) * item_bytes, math.prod(chunks) * item_bytes
    if array_bytes > max_array_bytes or chunk_bytes > max_chunk_bytes:
        raise ValueError("Array or chunk declared decoded bytes exceed their bounds")
    fill = header["fill_value"]
    if (kind == "U" and fill not in (None, "")) or (kind != "U" and (type(fill) not in (int, float) or fill != 0)):
        raise ValueError("Only empty/default fill declarations are supported")
    _compressor_config(header["compressor"])
    chunk_counts = [(length + chunk - 1) // chunk for length, chunk in zip(shape, chunks)]
    if math.prod(chunk_counts) > MAX_FILES:
        raise ValueError("Declared chunk coverage exceeds the file-count bound")
    return {"shape": tuple(shape), "kind": kind, "array_bytes": array_bytes, "chunk_bytes": chunk_bytes,
            "compressor": header["compressor"], "chunk_counts": chunk_counts}


def validate_prepared_zarr(path: Path, *, expected_shape: tuple[int, int] | None = None,
                           measurement_type: str | None = None, max_chunk_bytes: int = DEFAULT_CHUNK_BYTES,
                           max_metadata_bytes: int = 5_000_000, max_matrix_bytes: int = 100_000_000,
                           max_genes: int = MAX_GENES) -> dict[str, Any]:
    """Preflight actual frames on a verified, exclusively owned prepared snapshot.

This reads bytes and must be called only by an explicitly opted-in operation.
No expression values, biological metadata or numerical arrays are returned.
"""
    if (type(max_chunk_bytes) is not int or not 1 <= max_chunk_bytes <= DEFAULT_CHUNK_BYTES
            or type(max_metadata_bytes) is not int or not 1 <= max_metadata_bytes <= 5_000_000
            or type(max_matrix_bytes) is not int or not 1 <= max_matrix_bytes <= 100_000_000
            or type(max_genes) is not int or not 1 <= max_genes <= PUBLIC_MAX_BULK_FEATURES):
        raise ValueError("Prepared-store safety ceilings cannot be enlarged")
    root = Path(path)
    if not root.is_absolute() or ".." in root.parts or any(item.is_symlink() for item in (root, *root.parents)):
        raise ValueError("Prepared Zarr path must be absolute and free of symlink ancestors")
    root = root.resolve()
    if not root.is_dir():
        raise ValueError("Prepared Zarr root must be a directory")
    members = {}
    directories = set()
    for member in root.rglob("*"):
        name = member.relative_to(root).as_posix()
        if member.is_symlink():
            raise ValueError("Prepared Zarr members cannot be symlinks")
        if member.is_dir():
            directories.add(name)
            if name not in ARRAYS:
                raise ValueError("Unexpected prepared Zarr directory/sidecar")
        elif not is_prepared_zarr_member(name) or not member.is_file():
            raise ValueError("Unexpected prepared Zarr member/sidecar")
        else:
            members[name] = member
        if len(members) > MAX_FILES:
            raise ValueError("Prepared Zarr file count exceeds its bound")
    if directories != set(ARRAYS) or ".zgroup" not in members:
        raise ValueError("Prepared Zarr requires exactly the three known arrays")
    group = _json_object(_read_bounded(members[".zgroup"], 100_000))
    if group != {"zarr_format": 2}:
        raise ValueError("Prepared root metadata must identify only Zarr v2")
    metadata_bytes = 0
    arrays = {}
    for name, member in members.items():
        if name.endswith((".zgroup", ".zarray", ".zattrs")):
            content = _read_bounded(member, min(100_000, max_metadata_bytes))
            metadata_bytes += len(content)
            if metadata_bytes > max_metadata_bytes:
                raise ValueError("Prepared Zarr metadata exceeds its total bound")
            if name.endswith(".zattrs"):
                validate_empty_attributes(content)
            elif name.endswith(".zarray"):
                array = name.split("/")[0]
                arrays[array] = _array_header(_json_object(content), array, max_chunk_bytes=max_chunk_bytes,
                    max_array_bytes=max_matrix_bytes if array == "expression" else max_metadata_bytes)
    if set(arrays) != set(ARRAYS):
        raise ValueError("Every known array requires its declaration")
    shape = arrays["expression"]["shape"]
    if not 1 <= shape[0] <= max_genes or (expected_shape is not None and shape != tuple(expected_shape)):
        raise ValueError("Prepared expression shape differs from the bounded declared family")
    if arrays["gene_names"]["shape"] != (shape[0],) or arrays["sample_names"]["shape"] != (shape[1],):
        raise ValueError("Prepared label shapes do not match expression")
    if sum(arrays[name]["array_bytes"] for name in ("gene_names", "sample_names")) > max_metadata_bytes:
        raise ValueError("Decoded labels exceed the combined metadata bound")
    if measurement_type not in (None, "raw_counts", "continuous") or (measurement_type == "raw_counts" and arrays["expression"]["kind"] not in {"i", "u"}):
        raise ValueError("Prepared dtype is incompatible with the declared measurement")
    chunk_count = 0
    for name, description in arrays.items():
        expected = {name + "/" + ".".join(map(str, indices))
                    for indices in product(*(range(count) for count in description["chunk_counts"]))}
        actual = {member for member in members if member.startswith(name + "/") and not member.split("/")[1].startswith(".")}
        if actual != expected:
            raise ValueError("Prepared arrays require exact complete canonical chunk coverage")
        for member in sorted(expected):
            payload = _read_bounded(members[member], max_chunk_bytes + 65_536)
            validate_chunk_frame(payload, description["compressor"], description["chunk_bytes"], max_chunk_bytes=max_chunk_bytes)
            chunk_count += 1
    return {"format": "zarr_v2_complete_fixed_arrays", "shape": shape, "chunk_count": chunk_count,
            "max_decoded_chunk_bytes": max(description["chunk_bytes"] for description in arrays.values()),
            "attributes": "empty_only", "frame_bounds_verified": True}
