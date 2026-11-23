"""Synthetic image geometry checks; these do not validate tissue or segmentation."""

import base64
from copy import deepcopy
import hashlib
import importlib
import importlib.util
from io import BytesIO
import struct
import zlib

import numpy as np
from PIL import Image
import pytest


CAPABILITIES = ("spatial_tissue_overlay", "spatial_segmentation")


def api():
    name = "devbench.analysis.plot_adapters_spatial"
    assert importlib.util.find_spec(name) is not None, "spatial image adapters are not implemented"
    return importlib.import_module(name)


def images():
    name = "devbench.analysis.scientific_plot_images"
    assert importlib.util.find_spec(name) is not None, "bounded PNG validation is not implemented"
    return importlib.import_module(name)


def packet(capability):
    return deepcopy(next(p["data"] for p in fixture_packets() if p["capability"] == capability))


def panels(capability, data=None):
    return api().build_panels(capability, packet(capability) if data is None else data)


def marks(panel, kind):
    return [m for m in panel["marks"] if m["type"] == kind]


def test_inventory_has_actual_image_and_segmentation_adapters():
    assert set(api().CAPABILITIES) == set(CAPABILITIES)
    assert {p["capability"] for p in fixture_packets()} == set(CAPABILITIES)


@pytest.mark.parametrize("capability", CAPABILITIES)
def test_saved_synthetic_inputs_build_real_geometry_without_mutation(capability):
    from devbench.analysis.scientific_plot_contract import validate_panel

    data = packet(capability)
    original = deepcopy(data)
    result = panels(capability, data)
    assert data == original
    assert len(result) == 1
    p = result[0]
    validate_panel(p)
    assert p["capability"] == capability
    assert marks(p, "image") and any(m["type"] != "image" for m in p["marks"])
    assert p["axes"]["x"]["unit"] == p["axes"]["y"]["unit"] == "um"
    assert any("synthetic" in text.lower() for text in p["limitations"])
    assert any("intrinsic raster" in text.lower() for text in p["annotations"])
    assert any("physical aspect" in text.lower() for text in p["annotations"])


@pytest.mark.parametrize("capability", CAPABILITIES)
def test_required_evidence_and_unknown_fields_refuse(capability):
    for field in api().CAPABILITIES[capability]["required"]:
        data = packet(capability)
        del data[field]
        with pytest.raises(ValueError):
            panels(capability, data)
    data = packet(capability)
    data["guess_registration"] = True
    with pytest.raises(ValueError):
        panels(capability, data)
    with pytest.raises(ValueError):
        api().build_panels("invented", {})


def test_exact_original_quadrants_and_pixel_edge_affine():
    payload = packet("spatial_tissue_overlay")["image"]
    assert images().validate_image_payload(payload) == payload
    raw = images().image_png_bytes(payload)
    assert hashlib.sha256(raw).hexdigest() == payload["sha256"]
    assert raw == base64.b64decode(payload["png_base64"])
    assert images().image_rgba(payload).tolist() == [
        [[255, 0, 0, 255], [0, 255, 0, 255]],
        [[0, 0, 255, 255], [255, 255, 0, 255]],
    ]
    assert images().image_corners(payload) == [[10.0, 30.0], [14.0, 30.0], [14.0, 24.0], [10.0, 24.0]]
    payload["pixel_to_data"] = [0, 2, 3, 0, 10, 20]
    assert images().image_corners(payload) == [[10.0, 20.0], [10.0, 24.0], [16.0, 24.0], [16.0, 20.0]]


@pytest.mark.parametrize("field,value", [
    ("sha256", "0" * 64), ("width", 3), ("height", True),
    ("pixel_to_data", [1, 2, 2, 4, 0, 0]),
    ("pixel_to_data", [1, 0, 0, float("nan"), 0, 0]),
    ("pixel_to_data", [1, 0, 0, 1, 0]), ("coordinate_unit", "pixels"),
    ("png_base64", "https://example.invalid/tissue.png"),
])
def test_image_payload_rejects_false_integrity_geometry_or_units(field, value):
    payload = packet("spatial_tissue_overlay")["image"]
    payload[field] = value
    with pytest.raises(ValueError):
        images().validate_image_payload(payload)


@pytest.mark.parametrize("corruption", ["magic", "crc", "truncated", "trailing", "header_bomb", "no_image_data", "animated"])
def test_png_parser_rejects_corruption_and_bounded_decode_attacks(corruption):
    payload = packet("spatial_tissue_overlay")["image"]
    raw = base64.b64decode(payload["png_base64"])
    if corruption == "magic":
        raw = b"notPNG!!" + raw[8:]
    elif corruption == "crc":
        raw = raw[:29] + bytes([raw[29] ^ 1]) + raw[30:]
    elif corruption == "truncated":
        raw = raw[:-6]
    elif corruption == "trailing":
        raw += b"not PNG"
    elif corruption == "header_bomb":
        header = struct.pack(">IIBBBBB", 100000, 100000, 8, 2, 0, 0, 0)
        raw = raw[:8] + _chunk(b"IHDR", header) + raw[33:]
        payload["width"] = payload["height"] = 100000
    elif corruption == "no_image_data":
        raw = raw[:33] + _chunk(b"IEND", b"")
    elif corruption == "animated":
        raw = raw[:33] + _chunk(b"acTL", struct.pack(">II", 2, 0)) + raw[33:]
    payload["png_base64"] = base64.b64encode(raw).decode()
    payload["sha256"] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError):
        images().validate_image_payload(payload)


def test_png_byte_limit_precedes_base64_decoding():
    payload = packet("spatial_tissue_overlay")["image"]
    payload["png_base64"] = "A" * (4 * ((images().MAX_PNG_BYTES + 2) // 3) + 4)
    with pytest.raises(ValueError, match="budget"):
        images().validate_image_payload(payload)


@pytest.mark.parametrize("field,value", [
    ("coordinate_unit", []), ("coordinate_system_id", ""), ("registration_id", None),
    ("source", {"id": "unbound"}), ("width", 0), ("width", 16385),
    ("pixel_to_data", [10**500, 0, 0, 1, 0, 0]),
])
def test_malformed_image_metadata_fails_with_a_bounded_validation_error(field, value):
    payload = packet("spatial_tissue_overlay")["image"]
    payload[field] = value
    with pytest.raises(ValueError):
        images().validate_image_payload(payload)


def test_pixel_budget_and_unknown_external_location_fields_refuse():
    payload = packet("spatial_tissue_overlay")["image"]
    payload["width"] = payload["height"] = 2001
    with pytest.raises(ValueError, match="pixel_budget"):
        images().validate_image_payload(payload)
    payload = packet("spatial_tissue_overlay")["image"]
    payload["path"] = "/not/read/by/adapter.png"
    with pytest.raises(ValueError, match="fields"):
        images().validate_image_payload(payload)


def test_sixteen_bit_grayscale_preserves_intensity_order_without_clipping():
    payload = packet("spatial_tissue_overlay")["image"]
    original = np.array([[0, 256], [32768, 65535]], dtype=np.uint16)
    stream = BytesIO()
    Image.fromarray(original).save(stream, format="PNG")
    raw = stream.getvalue()
    payload["png_base64"] = base64.b64encode(raw).decode()
    payload["sha256"] = hashlib.sha256(raw).hexdigest()
    rgba = images().image_rgba(payload)
    assert rgba[:, :, 0].tolist() == [[0, 1], [128, 255]]
    assert rgba[:, :, 3].tolist() == [[255, 255], [255, 255]]
    assert images().image_png_bytes(payload) == raw


@pytest.mark.parametrize("change", [{"keys": ["spot:spot1"]}, {"context": False}, {"role": "biological_observation"}, {"style": {"opacity": 0.5}}])
def test_image_mark_cannot_impersonate_a_biological_observation(change):
    mark = {"type": "image", "keys": [], "group": "tissue_reference", "role": "tissue_reference", "context": True,
            "image": packet("spatial_tissue_overlay")["image"], **change}
    with pytest.raises(ValueError, match="context"):
        images().validate_image_mark(mark)


def test_image_context_has_no_biological_rows_and_keeps_original_bytes():
    p = panels("spatial_tissue_overlay")[0]
    image = marks(p, "image")[0]
    assert image["keys"] == [] and image["context"] is True
    assert image["role"] == "tissue_reference"
    images().validate_image_mark(image)
    assert image["image"] == packet("spatial_tissue_overlay")["image"]
    assert len(p["rows"]) == p["coverage"]["source_rows"] == 4
    assert all(r["entity_kind"] == "spot" for r in p["rows"])
    assert all(r["link"] == {"kind": "spot", "id": r["id"]} for r in p["rows"])


@pytest.mark.parametrize("part,field", [
    ("image_source", "sample_id"), ("image_source", "section_id"),
    ("image_source", "biological_unit_id"), ("image", "registration_id"),
    ("image", "coordinate_system_id"), ("spots", "sample_id"),
    ("spots", "section_id"), ("spots", "biological_unit_id"),
    ("spots", "registration_id"), ("spots", "coordinate_system_id"),
])
def test_overlay_rejects_different_cohort_or_registration(part, field):
    data = packet("spatial_tissue_overlay")
    target = data["image"]["source"] if part == "image_source" else data[part][0] if part == "spots" else data[part]
    target[field] = "unmatched"
    with pytest.raises(ValueError, match="match"):
        panels("spatial_tissue_overlay", data)


def test_overlay_rejects_unregistered_coordinates_and_mixed_units():
    data = packet("spatial_tissue_overlay")
    data["registration"]["status"] = "unregistered"
    with pytest.raises(ValueError, match="registered"):
        panels("spatial_tissue_overlay", data)
    data = packet("spatial_tissue_overlay")
    data["image"]["coordinate_unit"] = "mm"
    with pytest.raises(ValueError, match="match"):
        panels("spatial_tissue_overlay", data)


def test_missing_expression_preserves_spot_position_but_failed_coordinates_do_not_draw():
    p = panels("spatial_tissue_overlay")[0]
    rows = {r["id"]: r for r in p["rows"]}
    pts = marks(p, "point")
    assert [(m["x"], m["y"]) for m in pts] == [(11, 28.5), (13, 28.5), (11, 25.5)]
    assert [m.get("value") for m in pts] == [1.0, 4.0, None]
    assert rows["spot3"]["status"] == "recorded"
    assert rows["spot3"]["details"]["expression_reason"] == "assay measurement unavailable"
    assert rows["spot3"]["warnings"]
    assert rows["spot4"]["reason"] == "saved coordinate detection failed"
    assert rows["spot4"]["status"] == "excluded"
    assert p["coverage"]["missing_rows"] == 1
    assert p["color_scale"]["domain"] == [1.0, 4.0]


def test_declared_physical_spot_diameter_produces_physical_ellipses():
    data = packet("spatial_tissue_overlay")
    data["spot_diameter"] = 0.8
    p = panels("spatial_tissue_overlay", data)[0]
    assert not marks(p, "point")
    assert len(marks(p, "ellipse")) == 3
    assert all(m["rx"] == m["ry"] == pytest.approx(0.4) for m in marks(p, "ellipse"))


def test_saved_segmentation_polygons_and_cell_identity_are_exact():
    data = packet("spatial_segmentation")
    p = panels("spatial_segmentation", data)[0]
    polygons = marks(p, "polygon")
    assert len(polygons) == 2
    assert polygons[0]["points"] == data["cells"][0]["polygon"]
    assert polygons[1]["points"] == data["cells"][1]["polygon"]
    assert [m.get("value") for m in polygons] == [2.0, None]
    assert p["rows"][0]["x"] == pytest.approx(11)
    assert p["rows"][0]["y"] == pytest.approx(28.5)
    assert all(r["link"] == {"kind": "cell", "id": r["id"]} for r in p["rows"])
    assert p["rows"][2]["reason"] == "saved segmentation failed"
    assert p["rows"][0]["details"]["segmentation"] == data["segmentation"]


def test_segmentation_only_has_actual_boundaries_without_implied_image_or_expression():
    data = packet("spatial_segmentation")
    del data["image"], data["feature_id"], data["expression_unit"]
    for cell in data["cells"]:
        cell.pop("expression", None)
        cell.pop("expression_reason", None)
    p = panels("spatial_segmentation", data)[0]
    assert len(marks(p, "polygon")) == 2 and not marks(p, "image")
    assert p["color_scale"] is None
    assert all("value" not in m for m in p["marks"])
    assert any("image absent" in text.lower() for text in p["annotations"])
    assert any("expression absent" in text.lower() for text in p["annotations"])


def test_all_failed_segmentation_without_image_retains_failure_rows():
    data = packet("spatial_segmentation")
    del data["image"]
    for cell in data["cells"]:
        cell.update(polygon=None, status="failed", reason="saved segmentation failed")
    p = panels("spatial_segmentation", data)[0]
    assert p["marks"] == [] and p["availability"]["status"] == "unavailable"
    assert p["coverage"]["source_rows"] == p["coverage"]["missing_rows"] == 3
    assert all(r["reason"] == "saved segmentation failed" for r in p["rows"])


@pytest.mark.parametrize("polygon", [
    [[10, 27], [12, 27], [12, 30], [10, 30]],
    [[10, 27], [12, 30], [10, 30], [12, 27], [10, 27]],
    [[10, 27], [11, 27], [12, 27], [10, 27]],
    [[10, 27], [12, 27], [12, 27], [10, 30], [10, 27]],
    [[10, 27], [12, 27], [12, float("inf")], [10, 27]],
])
def test_segmentation_rejects_open_crossed_degenerate_or_nonfinite_polygons(polygon):
    data = packet("spatial_segmentation")
    data["cells"][0]["polygon"] = polygon
    with pytest.raises(ValueError):
        panels("spatial_segmentation", data)


@pytest.mark.parametrize("part,field", [
    ("segmentation", "sample_id"), ("segmentation", "registration_id"),
    ("segmentation", "coordinate_system_id"), ("cells", "sample_id"),
    ("cells", "biological_unit_id"), ("cells", "segmentation_id"),
])
def test_segmentation_must_belong_to_same_saved_scene(part, field):
    data = packet("spatial_segmentation")
    target = data[part][0] if part == "cells" else data[part]
    target[field] = "unmatched"
    with pytest.raises(ValueError, match="match"):
        panels("spatial_segmentation", data)


def test_unknown_expression_scale_duplicate_identity_and_unexplained_failure_refuse():
    data = packet("spatial_segmentation")
    del data["expression_unit"]
    with pytest.raises(ValueError, match="expression"):
        panels("spatial_segmentation", data)
    data = packet("spatial_segmentation")
    data["cells"][1]["id"] = data["cells"][0]["id"]
    with pytest.raises(ValueError, match="unique"):
        panels("spatial_segmentation", data)
    data = packet("spatial_segmentation")
    del data["cells"][2]["reason"]
    with pytest.raises(ValueError, match="reason"):
        panels("spatial_segmentation", data)


def _chunk(kind, payload):
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def fixture_packets() -> list[dict]:
    """Small, explicit synthetic pixels and polygons; no real tissue input."""
    pixels = np.array([[[255, 0, 0], [0, 255, 0]], [[0, 0, 255], [255, 255, 0]]], dtype=np.uint8)
    buffer = BytesIO()
    Image.fromarray(pixels).save(buffer, format="PNG")
    raw = buffer.getvalue()
    identity = {"sample_id": "synthetic-sample", "section_id": "synthetic-section", "biological_unit_id": "synthetic-donor"}
    registration = {"id": "saved-registration", **identity, "coordinate_system_id": "section-physical-frame",
                    "status": "registered", "method": "synthetic declared affine", "source_id": "synthetic-registration-output"}
    image = {"png_base64": base64.b64encode(raw).decode(), "sha256": hashlib.sha256(raw).hexdigest(),
             "width": 2, "height": 2, "pixel_to_data": [2, 0, 0, -3, 10, 30], "coordinate_unit": "um",
             "coordinate_system_id": registration["coordinate_system_id"], "registration_id": registration["id"],
             "source": {"id": "synthetic-four-quadrant-image", "kind": "synthetic", **identity}}
    membership = {**identity, "coordinate_system_id": registration["coordinate_system_id"], "registration_id": registration["id"]}
    common = {"image": image, "coordinate_unit": "um", "registration": registration,
              "feature_id": "synthetic-GENE", "expression_unit": "saved log-normalized expression",
              "source_label": "Synthetic geometry fixture; not biological or clinical validation."}
    spots = [
        {"id": "spot1", **membership, "x": 11, "y": 28.5, "expression": 1},
        {"id": "spot2", **membership, "x": 13, "y": 28.5, "expression": 4},
        {"id": "spot3", **membership, "x": 11, "y": 25.5, "expression": None, "expression_reason": "assay measurement unavailable"},
        {"id": "spot4", **membership, "x": None, "y": None, "expression": None,
         "status": "failed", "reason": "saved coordinate detection failed"},
    ]
    segmentation = {"id": "saved-segmentation", **membership, "method": "synthetic hand-declared polygon fixture",
                    "source_id": "synthetic-segmentation-output", "status": "saved"}
    cells = [
        {"id": "cell1", **membership, "segmentation_id": segmentation["id"], "expression": 2,
         "polygon": [[10, 27], [12, 27], [12, 30], [10, 30], [10, 27]]},
        {"id": "cell2", **membership, "segmentation_id": segmentation["id"], "expression": None,
         "expression_reason": "assay measurement unavailable", "polygon": [[12, 24], [14, 24], [14, 27], [12, 27], [12, 24]]},
        {"id": "cell3", **membership, "segmentation_id": segmentation["id"], "expression": None,
         "polygon": None, "status": "failed", "reason": "saved segmentation failed"},
    ]
    return [
        {"id": "synthetic-tissue-overlay", "capability": "spatial_tissue_overlay", "data": deepcopy({**common, "spots": spots})},
        {"id": "synthetic-cell-segmentation", "capability": "spatial_segmentation", "data": deepcopy({**common, "cells": cells, "segmentation": segmentation})},
    ]
