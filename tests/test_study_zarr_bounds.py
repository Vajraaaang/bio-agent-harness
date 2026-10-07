"""Compression/text plumbing and format declarations only; no expression fixtures."""

import gzip
import importlib
from pathlib import Path
import struct
import zlib

import pytest


def api():
    source = Path(__file__).resolve().parents[1] / "devbench" / "study" / "zarr_bounds.py"
    assert source.is_file(), "Bounded Zarr preflight is not implemented"
    return importlib.import_module("devbench.study.zarr_bounds")


@pytest.mark.parametrize("codec,compress", [("gzip", gzip.compress), ("zlib", zlib.compress)])
def test_compressed_text_requires_exact_bounded_complete_stream(codec, compress):
    text = b"plain-text compression plumbing" * 100
    payload = compress(text)
    assert api().validate_chunk_frame(payload, {"id": codec, "level": 1}, len(text), max_chunk_bytes=8192) == len(text)
    for bad, expected in ((payload, 5), (payload, len(text) + 1), (payload[:-1], len(text)),
                          (payload + b"trailing", len(text)), (payload + payload, len(text))):
        with pytest.raises(ValueError):
            api().validate_chunk_frame(bad, {"id": codec, "level": 1}, expected, max_chunk_bytes=8192)


def test_gzip_bomb_is_stopped_before_unbounded_output_or_flush(monkeypatch):
    payload = gzip.compress(b"plain text " * 100_000)
    original = zlib.decompressobj

    class GuardedDecoder:
        def __init__(self, *args):
            self.decoder = original(*args)

        def decompress(self, value, max_length):
            assert 0 < max_length <= 65
            return self.decoder.decompress(value, max_length)

        def flush(self, *args):
            pytest.fail("Unbounded flush is prohibited")

        def __getattr__(self, name):
            return getattr(self.decoder, name)

    monkeypatch.setattr(zlib, "decompressobj", GuardedDecoder)
    with pytest.raises(ValueError, match="decoded|size|bound"):
        api().validate_chunk_frame(payload, {"id": "gzip", "level": 1}, 64, max_chunk_bytes=4096)


def blosc_memcpy(text=b"header-only text plumbing"):
    return struct.pack("<BBBBIII", 2, 1, 2, 1, len(text), len(text), len(text) + 16) + text


def test_blosc_header_bounds_allocation_without_importing_a_decoder():
    payload = blosc_memcpy()
    assert api().validate_chunk_frame(payload, {"id": "blosc"}, len(payload) - 16) == len(payload) - 16
    for offset, value in ((4, 2**30), (8, 2**30), (12, len(payload) + 1)):
        bad = bytearray(payload)
        struct.pack_into("<I", bad, offset, value)
        with pytest.raises(ValueError):
            api().validate_chunk_frame(bytes(bad), {"id": "blosc"}, len(payload) - 16)
    for bad in (payload[:10], payload + b"trailing"):
        with pytest.raises(ValueError):
            api().validate_chunk_frame(bad, {"id": "blosc"}, len(payload) - 16)


@pytest.mark.parametrize("path", ["reference.json", "private/truth.json", ".zmetadata", "expression/reference.json",
                                 "expression/00.1", "gene_names/0.1", "expression/../0.0", ".zattrs/sidecar"])
def test_zarr_member_contract_rejects_sidecars_and_noncanonical_chunk_keys(path):
    assert api().is_prepared_zarr_member(path) is False


@pytest.mark.parametrize("path", [".zgroup", ".zattrs", "expression/.zarray", "expression/.zattrs",
                                 "expression/0.1", "gene_names/0", "sample_names/12"])
def test_zarr_member_contract_accepts_only_known_arrays_and_metadata(path):
    assert api().is_prepared_zarr_member(path) is True


def test_zarr_attributes_must_be_empty_and_json_keys_distinct():
    assert api().validate_empty_attributes(b"{}") == {}
    for payload in (b'{"private_truth": "hidden"}', b'{"x": 1, "x": 2}', b"[]"):
        with pytest.raises(ValueError):
            api().validate_empty_attributes(payload)


def test_uncompressed_text_must_match_declared_chunk_buffer():
    assert api().validate_chunk_frame(b"abc", None, 3) == 3
    with pytest.raises(ValueError):
        api().validate_chunk_frame(b"abc", None, 2)


def test_blosc_compressed_block_offsets_cannot_escape_the_actual_frame():
    payload = struct.pack("<BBBBIII", 2, 1, 0, 1, 64, 32, 40)
    payload += struct.pack("<II", 7, 500) + b"bounded-text-pad"
    assert len(payload) == 40
    with pytest.raises(ValueError, match="offset"):
        api().validate_chunk_frame(payload, {"id": "blosc"}, 64)


@pytest.mark.parametrize("compressor", [{"id": "pickle"}, {"id": "gzip", "private_truth": "hidden"},
    {"id": "zlib", "level": True}, {"id": "blosc", "cname": "unknown"}])
def test_codec_configuration_is_a_closed_bounded_declaration(compressor):
    with pytest.raises(ValueError):
        api().validate_chunk_frame(b"text", compressor, 4)


@pytest.mark.parametrize("expected,maximum", [(0, 64), (-1, 64), (65, 64), (True, 64), (4, 2**30)])
def test_frame_bounds_cannot_be_disabled_or_expanded(expected, maximum):
    with pytest.raises(ValueError):
        api().validate_chunk_frame(b"text", None, expected, max_chunk_bytes=maximum)
