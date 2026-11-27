"""Privacy checks for native data passed to the mounted forensic/QC adapters."""

import gzip
import struct
from contextlib import ExitStack

import h5py

from devbench.core.hdf5_custody import validate_hdf5_tree

from ._sources import BLOCK, MAX_HEADER_BYTES, check_time, clinical


def _tenx_sensitive(stream):
    from devbench.core.tenx import _checked_matrix_axes

    with h5py.File(stream, "r") as handle:
        validate_hdf5_tree(handle)
        if set(handle) != {"matrix"}:
            return True
        group = handle["matrix"]
        _checked_matrix_axes(group, 128 * BLOCK)
        if any(clinical(key) for key in group) or any(
            clinical(key) for key in group["features"]
        ):
            return True
        # Feature/barcode metadata can carry identifiers in values as well as keys.
        for dataset in (
            group["barcodes"],
            group["features/id"],
            group["features/name"],
        ):
            for start in range(0, len(dataset), 1024):
                if any(clinical(v) for v in dataset.asstr()[start : start + 1024]):
                    return True
    return False


def _bam_sensitive(reader):
    if reader.read(4) != b"BAM\x01":
        return True
    size = struct.unpack("<i", reader.read(4))[0]
    if not 0 <= size <= MAX_HEADER_BYTES:
        return True
    text = reader.read(size)
    return len(text) != size or clinical(text.decode("utf-8"))


def _index_sensitive(reader, fmt):
    if reader.read(4) != (fmt.upper().encode("ascii") + b"\x01"):
        return True
    if fmt == "bai":
        return False
    # Tabix stores eight header integers ending in the reference-name byte
    # length. CSI places its optional auxiliary header after min_shift/depth.
    integers = 8 if fmt == "tbi" else 3
    size = struct.unpack("<" + "i" * integers, reader.read(4 * integers))[-1]
    if not 0 <= size <= MAX_HEADER_BYTES:
        return True
    header = reader.read(size)
    return len(header) != size or clinical(header.decode("ascii", errors="replace"))


def _text_sensitive(reader, fmt, until):
    for line in iter(lambda: reader.readline(MAX_HEADER_BYTES + 1), b""):
        check_time(until)
        if (
            len(line) > MAX_HEADER_BYTES
            or b"\0" in line
            or clinical(line.decode("utf-8"))
        ):
            return True
        if fmt == "vcf" and line.startswith(b"#CHROM\t"):
            return False
    return fmt == "vcf"


def auxiliary_sensitive(stream, fmt, until):
    """Unsupported/opaque native layouts retain local private custody."""
    try:
        if fmt in {"rds", "annoy", "pt"}:
            # Binary reference internals cannot be screened for identifiers.
            return True
        if fmt == "h5":
            return _tenx_sensitive(stream)
        magic = stream.read(4)
        stream.seek(0)
        with ExitStack() as stack:
            reader = (
                stack.enter_context(gzip.GzipFile(fileobj=stream))
                if magic[:2] == b"\x1f\x8b"
                else stream
            )
            if fmt == "bam":
                return _bam_sensitive(reader)
            if fmt in {"bai", "tbi", "csi"}:
                return _index_sensitive(reader, fmt)
            return _text_sensitive(reader, fmt, until)
    except (ValueError, KeyError, OSError, UnicodeError, struct.error, EOFError):
        return True
