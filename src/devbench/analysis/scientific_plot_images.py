"""Bounded original PNG context for physical-coordinate scientific drawings.

This module accepts bytes embedded in a saved artifact, never a path or URL. PNG
integrity and a declared affine do not validate tissue identity or registration.
All affine coordinates address pixel edges; the original PNG is never resampled.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import math
import numbers
import re
import struct
import zlib
from io import BytesIO

from devbench.core.scientific_types import PHYSICAL_UNITS

MAX_PNG_BYTES = 8 * 1024**2
MAX_IMAGE_PIXELS = 4_000_000
MAX_IMAGE_DIMENSION = 16_384
_IMAGE_FIELDS = {"png_base64", "sha256", "width", "height", "pixel_to_data", "coordinate_unit",
                 "coordinate_system_id", "registration_id", "source"}
_SOURCE_FIELDS = {"id", "kind", "sample_id", "section_id", "biological_unit_id"}
_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 500 and "\x00" not in value


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _metadata(image):
    if not isinstance(image, dict) or set(image) != _IMAGE_FIELDS:
        raise ValueError("plot_image_fields_invalid; only bounded embedded PNG input is supported")
    for field in ("width", "height"):
        if isinstance(image[field], bool) or not isinstance(image[field], int) or not 0 < image[field] <= MAX_IMAGE_DIMENSION:
            raise ValueError("plot_image_dimension_budget_exceeded")
    if image["width"] * image["height"] > MAX_IMAGE_PIXELS:
        raise ValueError("plot_image_pixel_budget_exceeded")
    if not isinstance(image["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", image["sha256"]):
        raise ValueError("plot_image_sha256_invalid")
    if not isinstance(image["coordinate_unit"], str) or image["coordinate_unit"] not in PHYSICAL_UNITS:
        raise ValueError("plot_image_physical_coordinate_unit_required")
    if not all(_text(image[field]) for field in ("coordinate_system_id", "registration_id")):
        raise ValueError("plot_image_saved_coordinate_system_and_registration_required")
    source = image["source"]
    if (not isinstance(source, dict) or set(source) != _SOURCE_FIELDS
            or not all(_text(value) for value in source.values()) or source["kind"] not in {"synthetic", "retained"}):
        raise ValueError("plot_image_source_membership_required")
    affine = image["pixel_to_data"]
    if (not isinstance(affine, list) or len(affine) != 6
            or not all(_finite(v) for v in affine)):
        raise ValueError("plot_image_finite_affine6_required")
    a, b, c, d, _, _ = map(float, affine)
    determinant = a * d - b * c
    if not math.isfinite(determinant) or determinant == 0:
        raise ValueError("plot_image_nonsingular_affine_required")
    if any(not math.isfinite(v) for point in _corners(image) for v in point):
        raise ValueError("plot_image_affine_corners_nonfinite")


def _corners(image):
    a, b, c, d, e, f = map(float, image["pixel_to_data"])
    return [[float(a * x + c * y + e), float(b * x + d * y + f)]
            for x, y in ((0, 0), (image["width"], 0), (image["width"], image["height"]), (0, image["height"]))]


def _png_bytes(image):
    encoded = image["png_base64"]
    if not isinstance(encoded, str) or not encoded or len(encoded) > 4 * ((MAX_PNG_BYTES + 2) // 3):
        raise ValueError("plot_image_base64_byte_budget_exceeded")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("plot_image_base64_invalid") from error
    if not 0 < len(raw) <= MAX_PNG_BYTES:
        raise ValueError("plot_image_png_byte_budget_exceeded")
    if base64.b64encode(raw).decode("ascii") != encoded:
        raise ValueError("plot_image_base64_must_be_canonical")
    if hashlib.sha256(raw).hexdigest() != image["sha256"]:
        raise ValueError("plot_image_sha256_mismatch")
    if not raw.startswith(_SIGNATURE):
        raise ValueError("plot_image_png_signature_invalid")
    offset, count, seen_header, seen_data, seen_end = 8, 0, False, False, False
    while offset < len(raw):
        count += 1
        if count > 2048 or offset + 12 > len(raw):
            raise ValueError("plot_image_png_chunk_budget_or_truncation")
        length = struct.unpack_from(">I", raw, offset)[0]
        kind = raw[offset + 4:offset + 8]
        end = offset + length + 12
        if end > len(raw) or not re.fullmatch(b"[A-Za-z]{4}", kind):
            raise ValueError("plot_image_png_chunk_invalid")
        payload = raw[offset + 8:offset + 8 + length]
        expected_crc = struct.unpack_from(">I", raw, offset + 8 + length)[0]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != expected_crc:
            raise ValueError("plot_image_png_crc_mismatch")
        if not seen_header and kind != b"IHDR":
            raise ValueError("plot_image_png_IHDR_must_be_first")
        if kind == b"IHDR":
            if seen_header or length != 13:
                raise ValueError("plot_image_png_IHDR_invalid")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
            if width != image["width"] or height != image["height"]:
                raise ValueError("plot_image_png_dimensions_mismatch")
            depths = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
            if depth not in depths.get(color, set()) or compression != 0 or filtering != 0 or interlace not in {0, 1}:
                raise ValueError("plot_image_png_IHDR_encoding_invalid")
            seen_header = True
        elif kind in {b"acTL", b"fcTL", b"fdAT"}:
            raise ValueError("plot_image_animated_PNG_not_supported")
        elif kind == b"IDAT":
            seen_data = True
        elif kind == b"IEND":
            if length or not seen_data or end != len(raw):
                raise ValueError("plot_image_png_end_or_data_invalid")
            seen_end = True
        elif kind[0] < 97 and kind != b"PLTE":
            raise ValueError("plot_image_unknown_critical_PNG_chunk")
        offset = end
    if not seen_header or not seen_data or not seen_end:
        raise ValueError("plot_image_png_incomplete")
    return raw


def _decoded(image):
    from PIL import Image, UnidentifiedImageError

    _metadata(image)
    raw = _png_bytes(image)
    try:
        decoded = Image.open(BytesIO(raw))
        if decoded.format != "PNG" or decoded.size != (image["width"], image["height"]) or getattr(decoded, "n_frames", 1) != 1:
            raise ValueError("plot_image_decoded_PNG_mismatch")
        decoded.load()
    except (OSError, SyntaxError, UnidentifiedImageError, ValueError) as error:
        raise ValueError("plot_image_PNG_decode_invalid") from error
    return raw, decoded


def validate_image_payload(image):
    """Validate complete bytes, physical geometry and metadata; return input unchanged."""
    _, decoded = _decoded(image)
    decoded.close()
    return image


def image_png_bytes(image):
    """Return the exact integrity-verified original PNG bytes, without file I/O."""
    raw, decoded = _decoded(image)
    decoded.close()
    return raw


def image_rgba(image):
    """Return H×W×4 uint8 native-row-order pixels, with no spatial resampling."""
    import numpy as np

    _, decoded = _decoded(image)
    try:
        # Pillow's generic I;16 -> RGBA conversion clips, rather than scales,
        # 16-bit grayscale. Keep its high eight bits as native PNG decoders do.
        if decoded.mode in {"I", "I;16", "I;16B", "I;16L"}:
            values = np.asarray(decoded, dtype=np.uint16)
            gray = (values >> 8).astype(np.uint8)
            rgba = np.empty((*gray.shape, 4), dtype=np.uint8)
            rgba[:, :, :3] = gray[:, :, None]
            rgba[:, :, 3] = 255
            if "transparency" in decoded.info:
                rgba[:, :, 3] = np.where(values == decoded.info["transparency"], 0, 255)
            return rgba
        return np.array(decoded.convert("RGBA"), dtype=np.uint8)
    finally:
        decoded.close()


def image_corners(image):
    """Return physical pixel-edge corners (0,0), (w,0), (w,h), (0,h)."""
    validate_image_payload(image)
    return _corners(image)


def validate_image_mark(mark):
    """Validate context-only image marks; image sources never become biological rows."""
    if (not isinstance(mark, dict) or set(mark) != {"type", "keys", "group", "role", "context", "image"}
            or mark["type"] != "image" or mark["keys"] != [] or mark["group"] != "tissue_reference"
            or mark["role"] != "tissue_reference" or mark["context"] is not True):
        raise ValueError("plot_image_mark_must_be_explicit_tissue_context")
    validate_image_payload(mark["image"])
    return mark
