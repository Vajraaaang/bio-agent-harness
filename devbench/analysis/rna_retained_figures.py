"""Validate retained authored R figures without rendering substitute results.

Digests and decoding establish file retention and syntax, not that the scientific
procedure was appropriate or that its pixels have been independently reviewed.
The governed caller must independently supply the expected workflow and plot
provenance; a manifest cannot authenticate its own declarations.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import tarfile
import tempfile
from typing import Any, Mapping
import zlib


FORMAT = "devbench.rna-retained-figures/1"
MAX_FILE_BYTES = 16_777_216
MAX_BUNDLE_BYTES = 67_108_864
MAX_ARCHIVE_MEMBERS = 128
MAX_PIXELS = 16_000_000
_PROVENANCE = {"kind", "source_chunk_id", "source_chunk_sha256", "hypothesis_id",
    "sample_universe_id", "sample_universe_sha256", "gene_universe_id", "gene_universe_sha256"}
_PLOT_KEYS = {"plot_id", *_PROVENANCE, "axis_labels", "images", "plot_data", "state", "exclusions", "limitations"}
_PDF_OPERATORS = {item.encode("ascii") for item in (
    "b b* B B* BDC BI BMC BT BX c cm CS cs d d0 d1 Do DP EI EMC ET EX f F f* G g gs h i ID j J K k "
    "l m M MP n q Q re RG rg ri s S SC SCN sc scn sh T* Tc Td TD Tf Tj TJ TL Tm Tr Ts Tw Tz v w W W* y ' \""
).split()} | {b"INLINE IMAGE"}
_PDF_PAINTING = {item.encode("ascii") for item in "b b* B B* Do f F f* s S sh Tj TJ ' \"".split()} | {b"INLINE IMAGE"}


class RetainedFigureError(ValueError):
    """A retained figure is absent, invalid, changed, or outside its contract."""


def _keys(value: Any, expected: set[str]) -> None:
    if type(value) is not dict or set(value) != expected:
        raise RetainedFigureError("Retained figure fields differ from the closed contract")


def _text(value: Any, maximum: int = 1000) -> str:
    if type(value) is not str or not value.strip() or len(value) > maximum or "\x00" in value:
        raise RetainedFigureError("Use bounded nonempty retained-figure text")
    return value


def _sha(value: Any) -> str:
    if type(value) is not str or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise RetainedFigureError("Use an exact SHA-256 identity")
    return value


def _no_symlinks(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if stat.S_ISLNK(current.lstat().st_mode):
            raise RetainedFigureError("Symlinks are not retained-figure inputs")


def _read(path: Path, maximum: int = MAX_FILE_BYTES) -> bytes:
    if not path.is_absolute() or ".." in path.parts:
        raise RetainedFigureError("Retained paths must be absolute without parent traversal")
    _no_symlinks(path)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum:
            raise RetainedFigureError("Retained input must be a bounded nonempty regular file")
        body = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
    if identity(before) != identity(after) or identity(after) != identity(path.lstat()) or len(body) != after.st_size:
        raise RetainedFigureError("Retained input changed while reading")
    return body


def _relative(root: Path, value: Any) -> Path:
    text = _text(value, 256)
    relative = PurePosixPath(text)
    if (relative.is_absolute() or str(relative) != text or ".." in relative.parts
            or "\\" in text or len(relative.parts) > 4):
        raise RetainedFigureError("Retained paths must stay lexically within the figure root")
    path = root.joinpath(*relative.parts)
    _no_symlinks(path)
    return path


def _png(body: bytes) -> tuple[int, int]:
    if not body.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RetainedFigureError("Invalid retained PNG signature")
    offset, kinds = 8, []
    while offset < len(body):
        if offset + 12 > len(body):
            raise RetainedFigureError("Truncated retained PNG chunk")
        size = struct.unpack(">I", body[offset:offset + 4])[0]
        end = offset + 12 + size
        if end > len(body):
            raise RetainedFigureError("Truncated retained PNG body")
        kind = body[offset + 4:offset + 8]
        if zlib.crc32(body[offset + 4:end - 4]) != struct.unpack(">I", body[end - 4:end])[0]:
            raise RetainedFigureError("Retained PNG chunk checksum failed")
        kinds.append(kind)
        if kind == b"IEND" and (size != 0 or end != len(body)):
            raise RetainedFigureError("Trailing data after retained PNG")
        offset = end
    if not kinds or kinds[0] != b"IHDR" or kinds[-1] != b"IEND" or kinds.count(b"IHDR") != 1:
        raise RetainedFigureError("Incomplete retained PNG structure")
    try:
        from PIL import Image
        with Image.open(io.BytesIO(body)) as image:
            width, height = image.size
            if image.format != "PNG" or image.n_frames != 1 or width * height > MAX_PIXELS:
                raise RetainedFigureError("Retained PNG exceeds its single-image pixel bound")
            image.verify()
        with Image.open(io.BytesIO(body)) as image:
            image.load()
    except ImportError as error:
        raise RetainedFigureError("Declared Pillow reporting dependency is unavailable") from error
    except Exception as error:
        raise RetainedFigureError("Retained PNG could not be fully decoded") from error
    return width, height


def _pdf(body: bytes) -> tuple[float, float]:
    if not body.startswith(b"%PDF-") or not body.rstrip().endswith(b"%%EOF"):
        raise RetainedFigureError("Invalid retained PDF framing")
    try:
        from pypdf import Configuration, PdfReader, apply_configuration
        configuration = Configuration(
            maximum_declared_stream_length=MAX_FILE_BYTES,
            array_based_stream_maximum_output_length=MAX_FILE_BYTES,
            zlib_maximum_output_length=MAX_FILE_BYTES,
            lzw_maximum_output_length=MAX_FILE_BYTES,
            run_length_maximum_output_length=MAX_FILE_BYTES,
            jbig2_maximum_output_length=MAX_FILE_BYTES,
            brotli_maximum_output_length=MAX_FILE_BYTES,
            stream_decoding_work_maximum_length=2 * MAX_FILE_BYTES,
            stream_parsing_work_maximum_count=2_000_000,
            image_maximum_buffer_size=MAX_FILE_BYTES,
            page_tree_maximum_entries=16, page_tree_maximum_depth=8,
            jbig2dec_binary=None, disable_legacy_handling=True)
        with apply_configuration(configuration):
            return _pdf_document(PdfReader(io.BytesIO(body), strict=True))
    except ImportError as error:
        raise RetainedFigureError("Declared pypdf reporting dependency is unavailable") from error
    except Exception as error:
        raise RetainedFigureError("Retained PDF could not be parsed") from error


def _pdf_document(reader: Any) -> tuple[float, float]:
    """Check a page under the caller's locally bounded parser configuration."""
    if reader.is_encrypted or len(reader.pages) != 1:
        raise RetainedFigureError("Retain exactly one unencrypted PDF page per plot")
    if any(key in reader.trailer["/Root"] for key in ("/OpenAction", "/AA", "/AcroForm")):
        raise RetainedFigureError("Interactive retained PDF actions are unsupported")
    page = reader.pages[0]
    if any(key in page for key in ("/Annots", "/AA")):
        raise RetainedFigureError("Interactive retained PDF pages are unsupported")
    width, height = float(page.mediabox.width), float(page.mediabox.height)
    if not all(math.isfinite(value) and 0 < value <= 14400 for value in (width, height)):
        raise RetainedFigureError("Invalid retained PDF page dimensions")
    # The standard operator vocabulary is checked, but a renderer must still
    # inspect resources, placement, clipping and actual visible pixels.
    contents = page.get_contents()
    operators = [] if contents is None else [operator for _, operator in contents.operations]
    if any(operator not in _PDF_OPERATORS for operator in operators) or not set(operators) & _PDF_PAINTING:
        raise RetainedFigureError("Retained PDF lacks supported painting/text/image content")
    return width, height


def _table(body: bytes, record: dict[str, Any]) -> set[tuple[str, ...]]:
    _keys(record, {"path", "sha256", "format", "numeric_columns", "id_columns", "row_count"})
    numbers, identifiers = record["numeric_columns"], record["id_columns"]
    if (record["format"] != "tsv" or type(numbers) is not list or not 1 <= len(numbers) <= 16
            or type(identifiers) is not list or not 1 <= len(identifiers) <= 8
            or type(record["row_count"]) is not int or not 1 <= record["row_count"] <= 1_000_000):
        raise RetainedFigureError("Declare bounded finite long-form plot data and row identities")
    for name in numbers + identifiers:
        _text(name, 128)
    if len(set(numbers + identifiers)) != len(numbers + identifiers):
        raise RetainedFigureError("Plot-data columns must be distinct")
    try:
        reader = csv.DictReader(io.StringIO(body.decode("utf-8")), delimiter="\t", strict=True)
        columns = reader.fieldnames
        if columns is None or len(columns) > 32 or len(set(columns)) != len(columns) or not set(numbers + identifiers) <= set(columns):
            raise RetainedFigureError("Plot-data columns differ from the declared numeric/identity columns")
        seen: set[tuple[str, ...]] = set()
        for row in reader:
            if None in row or any(value is None or len(value) > 4096 or "\x00" in value for value in row.values()):
                raise RetainedFigureError("Invalid plot-data cell or row shape")
            key = tuple(_text(row[name], 256) for name in identifiers)
            if key in seen:
                raise RetainedFigureError("Duplicate composite plot-data row identity")
            seen.add(key)
            if len(seen) > record["row_count"] or any(not math.isfinite(float(_text(row[name], 128))) for name in numbers):
                raise RetainedFigureError("Plot data contain nonfinite values or exceed declared rows")
        if len(seen) != record["row_count"]:
            raise RetainedFigureError("Plot-data row count differs from its manifest")
        return seen
    except (UnicodeError, csv.Error, OverflowError, TypeError, ValueError) as error:
        raise RetainedFigureError("Invalid finite plot-data table") from error


def _exclusions(body: bytes, record: dict[str, Any], plot_data: dict[str, Any],
        included: set[tuple[str, ...]]) -> None:
    _keys(record, {"path", "sha256", "format", "id_columns", "row_count"})
    if (record["format"] != "tsv" or record["id_columns"] != plot_data["id_columns"]
            or type(record["row_count"]) is not int or not 0 <= record["row_count"] <= 1_000_000):
        raise RetainedFigureError("Exclusions must preserve explicit plot identities and row count")
    try:
        reader = csv.DictReader(io.StringIO(body.decode("utf-8")), delimiter="\t", strict=True)
        fields = reader.fieldnames
        if (fields is None or len(fields) > 32 or len(set(fields)) != len(fields)
                or not {*record["id_columns"], "status", "reason"} <= set(fields)):
            raise RetainedFigureError("Exclusions need explicit identifiers, status and reason")
        seen: set[tuple[str, ...]] = set()
        for row in reader:
            if None in row or any(value is None or len(value) > 4096 or "\x00" in value for value in row.values()):
                raise RetainedFigureError("Invalid exclusion row shape")
            key = tuple(_text(row[name], 256) for name in record["id_columns"])
            if key in seen or key in included:
                raise RetainedFigureError("Excluded plot identity is duplicated or also displayed")
            seen.add(key)
            _text(row["status"], 256)
            _text(row["reason"], 4000)
            if len(seen) > record["row_count"]:
                raise RetainedFigureError("Exclusion row count exceeds its manifest")
        if len(seen) != record["row_count"]:
            raise RetainedFigureError("Exclusion row count differs from its manifest")
    except (UnicodeError, csv.Error, TypeError, ValueError) as error:
        raise RetainedFigureError("Invalid explicit plot exclusions") from error


def validate_retained_figures(manifest_path: str | Path, *, expected_manifest_sha256: str,
        workflow_sha256: str, expected_plots: Mapping[str, Mapping[str, str]],
        required_image_formats: set[str] | frozenset[str] = frozenset({"png", "pdf"})) -> dict[str, Any]:
    """Revalidate one exact retained R-figure bundle against trusted expectations.

    Args:
        manifest_path: Absolute path to the governed retained figure manifest.
        expected_manifest_sha256: Independently retained manifest byte digest.
        workflow_sha256: Exact independently accepted workflow byte identity.
        expected_plots: Exact plot IDs and source/hypothesis/universe provenance.
        required_image_formats: Required formats, normally both authored PNG/PDF.

    Returns:
        File/provenance records with syntax checks and explicit absent pixel review.

    Raises:
        RetainedFigureError: Missing, unbound, malformed, or changed retained input.
    """
    path = Path(manifest_path)
    if not path.is_absolute() or path.name != "manifest.json":
        raise RetainedFigureError("Use the absolute retained figure manifest.json path")
    body = _read(path, 1_000_000)
    if hashlib.sha256(body).hexdigest() != _sha(expected_manifest_sha256):
        raise RetainedFigureError("Retained manifest digest differs from the independent seal")
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise RetainedFigureError("Duplicate retained manifest JSON field")
            result[key] = value
        return result
    try:
        manifest = json.loads(body, object_pairs_hook=unique)
    except (ValueError, UnicodeError) as error:
        raise RetainedFigureError("Invalid retained manifest JSON") from error
    _keys(manifest, {"format", "workflow_sha256", "plots"})
    if manifest["format"] != FORMAT or manifest["workflow_sha256"] != _sha(workflow_sha256):
        raise RetainedFigureError("Retained workflow identity differs from the accepted workflow")
    if (not isinstance(expected_plots, Mapping) or not 1 <= len(expected_plots) <= 64
            or type(manifest["plots"]) is not list or len(manifest["plots"]) != len(expected_plots)
            or not required_image_formats or not required_image_formats <= {"png", "pdf"}):
        raise RetainedFigureError("Declare exact bounded plot and image formats coverage")
    retained = {path.name}
    total = len(body)
    seen: set[str] = set()
    for plot in manifest["plots"]:
        _keys(plot, _PLOT_KEYS)
        key = _text(plot["plot_id"], 128)
        if key in seen or key not in expected_plots:
            raise RetainedFigureError("Duplicate or unexpected retained plot identity")
        seen.add(key)
        _keys(dict(expected_plots[key]), _PROVENANCE)
        if {field: plot[field] for field in _PROVENANCE} != dict(expected_plots[key]):
            raise RetainedFigureError("Retained plot provenance differs from frozen expectations")
        for field in _PROVENANCE:
            _sha(plot[field]) if field.endswith("sha256") else _text(plot[field], 256)
        _keys(plot["axis_labels"], {"x", "y"})
        if not (plot["kind"] == "conceptual_diagram" and plot["axis_labels"] == {"x": "", "y": ""}):
            for label in plot["axis_labels"].values():
                _text(label, 256)
        _text(plot["limitations"], 4000)
        images = plot["images"]
        if type(images) is not list or len(images) != len(required_image_formats):
            raise RetainedFigureError("Retained image formats coverage differs from the frozen contract")
        _keys(plot["plot_data"], {"path", "sha256", "format", "numeric_columns", "id_columns", "row_count"})
        _keys(plot["state"], {"path", "sha256", "format"})
        _keys(plot["exclusions"], {"path", "sha256", "format", "id_columns", "row_count"})
        for image in images:
            _keys(image, {"path", "sha256", "format", "width", "height"})
        formats: set[str] = set()
        included: set[tuple[str, ...]] = set()
        for artifact in [*images, plot["plot_data"], plot["state"], plot["exclusions"]]:
            if type(artifact) is not dict:
                raise RetainedFigureError("Retained file declaration must be an object")
            file_path = _relative(path.parent, artifact.get("path"))
            if artifact["path"] in retained:
                raise RetainedFigureError("Each retained file must have one unambiguous owner")
            retained.add(artifact["path"])
            file_body = _read(file_path)
            total += len(file_body)
            if total > MAX_BUNDLE_BYTES or hashlib.sha256(file_body).hexdigest() != _sha(artifact["sha256"]):
                raise RetainedFigureError("Retained file digest or aggregate byte bound failed")
            if artifact is plot["plot_data"]:
                if file_path.suffix != ".tsv":
                    raise RetainedFigureError("Retain explicit TSV plot-data files")
                included = _table(file_body, artifact)
                continue
            if artifact is plot["state"]:
                _keys(artifact, {"path", "sha256", "format"})
                if artifact["format"] != "rds" or file_path.suffix != ".rds":
                    raise RetainedFigureError("Retain opaque RDS state without loading it")
                continue
            if artifact is plot["exclusions"]:
                if file_path.suffix != ".tsv":
                    raise RetainedFigureError("Retain explicit TSV exclusion files")
                _exclusions(file_body, artifact, plot["plot_data"], included)
                continue
            _keys(artifact, {"path", "sha256", "format", "width", "height"})
            kind = artifact["format"]
            if kind not in required_image_formats or kind in formats or file_path.suffix != "." + kind:
                raise RetainedFigureError("Unexpected or duplicated retained image format")
            formats.add(kind)
            dimensions = _png(file_body) if kind == "png" else _pdf(file_body)
            if any(type(artifact[name]) not in (int, float) or not math.isfinite(artifact[name]) for name in ("width", "height")) or dimensions != (artifact["width"], artifact["height"]):
                raise RetainedFigureError("Retained decoded dimensions differ from the manifest")
    actual: set[str] = set()
    for directory, dirs, files in os.walk(path.parent, followlinks=False):
        if dirs:
            raise RetainedFigureError("Retain a flat figure root without additional directories")
        for name in [*dirs, *files]:
            candidate = Path(directory) / name
            _no_symlinks(candidate)
            if candidate.is_file():
                actual.add(candidate.relative_to(path.parent).as_posix())
            else:
                raise RetainedFigureError("Only regular files may exist in the retained figure root")
        if len(actual) > MAX_ARCHIVE_MEMBERS:
            raise RetainedFigureError("Retained figure root exceeds its closed file inventory")
    if actual != retained:
        raise RetainedFigureError("Missing or extra unretained files in the figure root")
    return {"format": FORMAT, "manifest_path": str(path), "manifest_sha256": expected_manifest_sha256,
        "workflow_sha256": workflow_sha256, "plots": manifest["plots"],
        "validation_scope": "retained_bytes_provenance_png_decode_pdf_structure_operator_vocabulary_geometry_and_finite_plot_data",
        "pixel_review": "not_performed", "scientific_review": "not_performed"}


def materialize_retained_figure_archive(archive_path: str | Path, *, expected_archive_sha256: str,
        expected_manifest_sha256: str, workflow_sha256: str, expected_plots: Mapping[str, Mapping[str, str]],
        workspace_root: str | Path, output_directory: str | Path,
        required_image_formats: set[str] | frozenset[str] = frozenset({"png", "pdf"})) -> dict[str, Any]:
    """Safely retain one flat bounded figure tar.gz in a fresh owned directory.

    The source archive remains unchanged. No archive member is executed, loaded
    as R state, or trusted to select its destination. Invalid materialization is
    removed; only this function's freshly created directory may be cleaned up.
    """
    archive_path, workspace, output = Path(archive_path), Path(workspace_root), Path(output_directory)
    if not all(item.is_absolute() for item in (archive_path, workspace, output)):
        raise RetainedFigureError("Use absolute archive, workspace and destination paths")
    _no_symlinks(workspace)
    try:
        relative = output.relative_to(workspace)
    except ValueError as error:
        raise RetainedFigureError("Figure materialization must stay inside the assigned workspace") from error
    if not relative.parts or ".." in relative.parts or not output.parent.is_dir():
        raise RetainedFigureError("Use a fresh figure directory beneath an existing workspace directory")
    _no_symlinks(output.parent)
    if output.exists() or output.is_symlink():
        raise RetainedFigureError("Figure materialization may not overwrite an existing destination")
    compressed = _read(archive_path, MAX_BUNDLE_BYTES)
    if hashlib.sha256(compressed).hexdigest() != _sha(expected_archive_sha256):
        raise RetainedFigureError("Retained archive digest differs from the independent seal")
    maximum_expanded = MAX_BUNDLE_BYTES + 1_000_000
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(compressed), mode="rb") as stream:
            expanded = stream.read(maximum_expanded + 1)
    except (OSError, EOFError) as error:
        raise RetainedFigureError("Invalid retained gzip archive") from error
    if len(expanded) > maximum_expanded:
        raise RetainedFigureError("Retained archive exceeds its expanded byte limit")
    owned = False
    try:
        with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as archive:
            members, names, total, final_offset = [], set(), 0, 0
            for member in archive:
                name = PurePosixPath(member.name)
                if (len(members) >= MAX_ARCHIVE_MEMBERS or len(name.parts) != 1 or str(name) != member.name or name.is_absolute()
                        or name.name in {".", ".."} or "\\" in member.name or len(member.name) > 256
                        or member.name in names or not member.isreg() or member.type not in {tarfile.REGTYPE, tarfile.AREGTYPE}
                        or member.linkname or member.sparse is not None or member.pax_headers
                        or member.offset_data != member.offset + 512
                        or not 0 < member.size <= (1_000_000 if member.name == "manifest.json" else MAX_FILE_BYTES)):
                    raise RetainedFigureError("Unsupported, duplicate, unsafe or oversized retained archive member")
                names.add(member.name)
                members.append(member)
                total += member.size
                final_offset = member.offset_data + ((member.size + 511) // 512) * 512
                if total > MAX_BUNDLE_BYTES or final_offset > maximum_expanded:
                    raise RetainedFigureError("Retained archive exceeds its aggregate content limit")
            if ("manifest.json" not in names or len(expanded) < final_offset + 1024
                    or any(expanded[final_offset:])):
                raise RetainedFigureError("Retained archive lacks its manifest or canonical zero trailer")
            output.mkdir(mode=0o700)
            owned = True
            for member in members:
                source = archive.extractfile(member)
                if source is None:
                    raise RetainedFigureError("Retained archive member lacks regular content")
                with source:
                    content = source.read(member.size + 1)
                if len(content) != member.size:
                    raise RetainedFigureError("Retained archive member is truncated")
                descriptor = os.open(output / member.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(descriptor, "wb") as destination:
                    destination.write(content)
        result = validate_retained_figures(output / "manifest.json", expected_manifest_sha256=expected_manifest_sha256,
            workflow_sha256=workflow_sha256, expected_plots=expected_plots, required_image_formats=required_image_formats)
        return {**result, "archive_path": str(archive_path), "archive_sha256": expected_archive_sha256}
    except Exception:
        if owned:
            shutil.rmtree(output)
        raise


def normalize_retained_figure_validation(validation: Mapping[str, Any]) -> dict[str, Any]:
    """Remove temporary paths from a revalidated record, without granting trust."""
    return json.loads(json.dumps({key: value for key, value in validation.items()
        if key not in {"manifest_path", "archive_path"}}, allow_nan=False))


def validate_authored_figure_expectations(expectation: Mapping[str, Any], authored_manifest: Mapping[str, Any], *,
        sample_universe_sha256: str, gene_universe_sha256: str) -> dict[str, dict[str, str]]:
    """Require every active authored figure without enabling eval-false sections.

    Callers must verify the authored manifest bytes against its immutable source
    reference before passing its parsed value. This function grants no execution
    permission; it closes the independently frozen full-procedure denominator.
    """
    _keys(expectation, {"workflow_sha256", "workflow_manifest_ref", "expected_plots", "required_image_formats"})
    _keys(expectation["workflow_manifest_ref"], {"path", "sha256", "size_bytes"})
    for value in (sample_universe_sha256, gene_universe_sha256, expectation["workflow_sha256"]):
        _sha(value)
    if (expectation["workflow_manifest_ref"]["sha256"] != expectation["workflow_sha256"]
            or expectation["required_image_formats"] != ["png", "pdf"]
            or authored_manifest.get("format") != "devbench.airway-original-workflow/1"
            or authored_manifest.get("workflow_id") != "original-airway-2015-full-processed-v1"
            or authored_manifest.get("source", {}).get("sha256") != "834e806bfb6020606378a08b75b749a967502fec0550de12724da9f6aa076d4c"):
        raise RetainedFigureError("Authored figure expectation source or both-image-format binding differs")
    chunks = authored_manifest.get("chunks")
    plots = expectation["expected_plots"]
    if type(chunks) is not list or not 1 <= len(chunks) <= 256 or type(plots) is not dict:
        raise RetainedFigureError("Declare exact active authored figure coverage")
    active: dict[str, Any] = {}
    seen: set[str] = set()
    for chunk in chunks:
        if type(chunk) is not dict or type(chunk.get("author_eval")) is not bool:
            raise RetainedFigureError("Authored chunk evaluation state must be explicit")
        key = _text(chunk.get("chunk_id"), 128)
        if key in seen:
            raise RetainedFigureError("Duplicate authored source chunk identity")
        seen.add(key)
        if chunk["author_eval"] and chunk.get("figure") is not None:
            active[key] = chunk
    if not 1 <= len(active) <= 64 or set(plots) != set(active):
        raise RetainedFigureError("Expectation must cover every active authored figure and no eval-false figure")
    for key, plot in plots.items():
        _keys(plot, _PROVENANCE)
        chunk = active[key]
        expected = {"kind": chunk["figure"]["kind"], "source_chunk_id": key,
            "source_chunk_sha256": chunk["code_sha256"], "hypothesis_id": chunk["figure"]["hypothesis_id"],
            "sample_universe_id": "airway2015-eight-libraries", "sample_universe_sha256": sample_universe_sha256,
            "gene_universe_id": "airway2015-original-input-genes", "gene_universe_sha256": gene_universe_sha256}
        if plot != expected:
            raise RetainedFigureError("Expectation changes an active authored figure's provenance or universe")
    return json.loads(json.dumps(plots, allow_nan=False))


def bind_retained_figure_expectation(template_path: str | Path, workflow_manifest_path: str | Path, *,
        expected_template_sha256: str, expected_workflow_manifest_sha256: str,
        sample_universe_id: str, sample_universe_sha256: str,
        gene_universe_id: str, gene_universe_sha256: str) -> dict[str, Any]:
    """Bind frozen author chunk pins to explicitly supplied original universes.

    This produces a pre-run expectation, not an acceptance or a rendered result.
    Template and source identities must already be independently retained.
    """
    template_path, workflow_path = Path(template_path), Path(workflow_manifest_path)
    template_body, workflow_body = _read(template_path, 1_000_000), _read(workflow_path, 1_000_000)
    if (hashlib.sha256(template_body).hexdigest() != _sha(expected_template_sha256)
            or hashlib.sha256(workflow_body).hexdigest() != _sha(expected_workflow_manifest_sha256)):
        raise RetainedFigureError("Frozen figure template or authored workflow source digest differs")
    template, workflow = json.loads(template_body), json.loads(workflow_body)
    bound = type(template) is dict and set(template) == {"workflow_sha256", "workflow_manifest_ref", "expected_plots", "required_image_formats"}
    if bound:
        _keys(template["workflow_manifest_ref"], {"path", "sha256", "size_bytes"})
        if template["workflow_manifest_ref"] != {"path": str(workflow_path), "sha256": expected_workflow_manifest_sha256, "size_bytes": len(workflow_body)}:
            raise RetainedFigureError("Already-bound template refers to a different authored source")
        items = []
        for key, item in template["expected_plots"].items():
            _keys(item, _PROVENANCE)
            if (item["sample_universe_id"] != sample_universe_id or item["sample_universe_sha256"] != sample_universe_sha256
                    or item["gene_universe_id"] != gene_universe_id or item["gene_universe_sha256"] != gene_universe_sha256):
                raise RetainedFigureError("Do not overwrite universes in an already-bound expectation")
            items.append({"plot_id": key, **item, "original_eval": True})
    else:
        _keys(template, {"format", "workflow_sha256", "required_image_formats", "expected_plots", "status", "actual_rendered_figures"})
        if template["format"] != "devbench.airway-full-authored-figure-template/1":
            raise RetainedFigureError("Unknown original authored figure template")
        items = template["expected_plots"]
    if (template["workflow_sha256"] != expected_workflow_manifest_sha256
            or template["required_image_formats"] != ["png", "pdf"]
            or workflow.get("format") != "devbench.airway-original-workflow/1"
            or workflow.get("workflow_id") != "original-airway-2015-full-processed-v1"
            or workflow.get("source", {}).get("sha256") != "834e806bfb6020606378a08b75b749a967502fec0550de12724da9f6aa076d4c"):
        raise RetainedFigureError("Frozen template does not bind the exact original authored workflow")
    for value in (sample_universe_id, gene_universe_id):
        _text(value, 256)
    for value in (sample_universe_sha256, gene_universe_sha256):
        _sha(value)
    chunks = {chunk["chunk_id"]: chunk for chunk in workflow["chunks"]
        if chunk.get("figure") is not None and chunk.get("author_eval") is True}
    plots: dict[str, dict[str, str]] = {}
    for item in items:
        key = _text(item["plot_id"], 128)
        chunk = chunks.get(key)
        if (key in plots or chunk is None or item["source_chunk_id"] != key
                or item["source_chunk_sha256"] != chunk["code_sha256"]
                or item["kind"] != chunk["figure"]["kind"]
                or item["hypothesis_id"] != chunk["figure"]["hypothesis_id"]
                or item["original_eval"] is not True):
            raise RetainedFigureError("Figure template invents or changes an authored source chunk")
        plots[key] = {field: item[field] for field in ("kind", "source_chunk_id", "source_chunk_sha256", "hypothesis_id")}
        plots[key].update(sample_universe_id=sample_universe_id, sample_universe_sha256=sample_universe_sha256,
            gene_universe_id=gene_universe_id, gene_universe_sha256=gene_universe_sha256)
    if set(plots) != set(chunks) or not 1 <= len(plots) <= 64:
        raise RetainedFigureError("Frozen template omits an authored active figure")
    result = {"workflow_sha256": expected_workflow_manifest_sha256,
        "workflow_manifest_ref": {"path": str(workflow_path), "sha256": expected_workflow_manifest_sha256,
            "size_bytes": len(workflow_body)}, "expected_plots": plots, "required_image_formats": ["png", "pdf"]}
    validate_authored_figure_expectations(result, workflow, sample_universe_sha256=sample_universe_sha256,
        gene_universe_sha256=gene_universe_sha256)
    return result


def collect_retained_figure_descriptors(run_receipts: list[Any], assessment_plan: Mapping[str, Any], *,
        artifact_service: Any, workspace: str | Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Derive figure evidence solely from actual governed runs and a frozen plan.

    This reads receipts and outputs; it executes no model, R process, or analysis.
    Injected test executors retain their own synthetic provenance and establish
    no qualification merely by producing valid archive bytes.
    """
    from .checkpoints import digest
    from .rna_comparison_outputs import _plan, _runs
    from .rna_execution_inputs import _artifact, _read_json
    from .rna_publication_profiles import read_reference_json

    workspace = Path(workspace)
    service_root = Path(artifact_service.root)
    if not workspace.is_absolute() or not service_root.is_relative_to(workspace):
        raise RetainedFigureError("Retained figure service must be within its assigned workspace")
    _, _, workflow, bridge = _plan(assessment_plan, artifact_service)
    runs = _runs(run_receipts, artifact_service, workflow, bridge)
    descriptors, validations = [], []
    for run in runs:
        reference = run["parameters"].get("retained_figure_expectation_ref")
        if reference is None or run["status"] != "succeeded":
            continue
        method = artifact_service.method(run["method_version_id"])
        if (method["contract"]["implementation"]["kind"] != "oci"
                or run["parameters"].get("workflow_id") != "original-airway-2015-full-processed-v1"):
            raise RetainedFigureError("Only the explicit authored-airway OCI figure contract is supported")
        expectation = read_reference_json(reference, workspace=workspace, service=artifact_service)
        _keys(expectation, {"workflow_sha256", "workflow_manifest_ref", "expected_plots", "required_image_formats"})
        authored_ref = expectation["workflow_manifest_ref"]
        _keys(authored_ref, {"path", "sha256", "size_bytes"})
        if authored_ref["sha256"] != expectation["workflow_sha256"]:
            raise RetainedFigureError("Frozen authored workflow locator differs from its SHA-256 identity")
        authored = read_reference_json(authored_ref, workspace=workspace, service=artifact_service)
        if (authored.get("format") != "devbench.airway-original-workflow/1"
                or authored.get("source", {}).get("sha256") != "834e806bfb6020606378a08b75b749a967502fec0550de12724da9f6aa076d4c"):
            raise RetainedFigureError("Frozen authored workflow differs from the exact recovered author source")
        validate_authored_figure_expectations(expectation, authored,
            sample_universe_sha256=run["parameters"].get("sample_universe_sha256"),
            gene_universe_sha256=run["parameters"].get("gene_universe_sha256"))
        formats = expectation["required_image_formats"]
        if (type(formats) is not list or len(formats) != 2 or set(formats) != {"png", "pdf"}
                or expectation["workflow_sha256"] != run["parameters"].get("workflow_sha256")):
            raise RetainedFigureError("Frozen authored workflow and both image formats are required")
        if not {"result_manifest", "airway_figures"} <= set(run["outputs"]):
            raise RetainedFigureError("Actual run lacks the required manifest and figure archive outputs")
        manifest_ref, manifest_path = _artifact(artifact_service, run["outputs"]["result_manifest"])
        archive_ref, archive_path = _artifact(artifact_service, run["outputs"]["airway_figures"])
        if manifest_ref["artifact_type"] != "json" or archive_ref["artifact_type"] != "file":
            raise RetainedFigureError("Actual retained figure ports differ from their frozen artifact types")
        result = _read_json(manifest_path, 1_000_000, expected=manifest_ref)
        if (type(result) is not dict or result.get("format") != "devbench.airway-full-native-result/1"
                or result.get("workflow_id") != run["parameters"]["workflow_id"]
                or result.get("workflow_sha256") != expectation["workflow_sha256"]):
            raise RetainedFigureError("Actual result manifest differs from its frozen authored workflow")
        retained = result.get("retained_figures")
        _keys(retained, {"archive_port", "manifest_sha256"})
        if retained["archive_port"] != "airway_figures":
            raise RetainedFigureError("Actual result manifest selects an unsupported archive port")
        with tempfile.TemporaryDirectory(dir=service_root, prefix="retained-figure-check-") as scratch:
            validation = materialize_retained_figure_archive(archive_path,
                expected_archive_sha256=archive_ref["sha256"], expected_manifest_sha256=retained["manifest_sha256"],
                workflow_sha256=expectation["workflow_sha256"], expected_plots=expectation["expected_plots"],
                workspace_root=workspace, output_directory=Path(scratch) / "figures", required_image_formats=set(formats))
            normalized = normalize_retained_figure_validation(validation)
        descriptor = {"run_id": run["run_id"], "port": "airway_figures", "artifact_ref": archive_ref,
            "archive_sha256": archive_ref["sha256"], "manifest_sha256": retained["manifest_sha256"],
            "expectation_ref": reference, "validation_sha256": digest(normalized)}
        descriptors.append(descriptor)
        validations.append(normalized)
    return descriptors, validations


def load_retained_figure_validations(bundle: Mapping[str, Any], *, artifact_service: Any,
        workspace: str | Path) -> list[dict[str, Any]]:
    """Recompute actual receipt/plan/archive evidence; accept no caller summaries."""
    if (type(bundle) is not dict or bundle.get("format") != "devbench.rna-analysis-output-bundle/3"
            or type(bundle.get("retained_figures")) is not list or type(bundle.get("runs")) is not list):
        raise RetainedFigureError("Retained authored figures require output bundle /3")
    descriptors, validations = collect_retained_figure_descriptors([run["run_id"] for run in bundle["runs"]],
        bundle["assessment_plan_ref"], artifact_service=artifact_service, workspace=workspace)
    if descriptors != bundle["retained_figures"]:
        raise RetainedFigureError("Retained figure descriptors differ from actual governed outputs")
    return validations
