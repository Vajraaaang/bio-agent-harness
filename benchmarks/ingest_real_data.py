"""Bounded processed ArrayExpress ingestion with explicit synthetic fallback provenance.

--subset keeps the first 200 genes and at most 60 embryonic columns by default.
This deterministic prefix is not representative sampling. HTTP subset reads stop
at the requested complete gene row; bytes_received counts response-body bytes
returned to this client, excluding HTTP/TLS framing and transport buffering.
"""
import argparse
import csv
import hashlib
import io
import json
import logging
import math
import re
import ssl
import time
from http.client import HTTPException
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Dict, Optional
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

import certifi
import numpy as np
import pandas as pd
import zarr
from numcodecs import Blosc

from benchmarks import DATA_DIRECTORY
from benchmarks.generate_synthetic import generate_synthetic, make_dataset
from benchmarks.public_data import _row_parser, sha256_file
from devharness_core.stages import parse_stages
from devharness_core.storage import ChunkedExpressionStore

LOGGER = logging.getLogger(__name__)
PROCESSED_URL = "https://www.ebi.ac.uk/biostudies/files/E-MTAB-6798/Mouse.RPKM.txt"
MAX_BYTES = 200_000_000
MAX_HEADER_BYTES = 1_000_000
MAX_LINE_BYTES = 4_000_000


def _validate_url(url):
    parsed = urlparse(url)
    path = unquote(parsed.path).lower()
    if (parsed.scheme != "https" or not path.endswith((".txt", ".tsv", ".csv"))
            or re.search(r"\.(bam|sam|cram|fastq|fq)(\.|$)", path)):
        raise ValueError("Only HTTPS processed text tables are accepted; raw archives are prohibited")


def _validate_budget(timeout_sec, max_bytes):
    if (isinstance(timeout_sec, bool) or not math.isfinite(timeout_sec) or timeout_sec <= 0
            or isinstance(max_bytes, bool) or not math.isfinite(max_bytes)
            or not 0 < max_bytes <= MAX_BYTES or int(max_bytes) != max_bytes):
        raise ValueError("Timeout must be finite and positive; byte budget must be 1–200,000,000")


def _response(url, timeout_sec):
    request = Request(url, headers={"User-Agent": "devharness/0.1 processed-table-client", "Accept-Encoding": "identity"})
    response = urlopen(request, timeout=min(timeout_sec, 10), context=ssl.create_default_context(cafile=certifi.where()))
    try:
        _validate_url(response.url)
        if response.headers.get("Content-Encoding", "identity").lower() not in ("", "identity"):
            raise ValueError("Compressed HTTP responses are not accepted for processed text")
    except Exception:
        response.close()
        raise
    return response


def download_processed(url: str, destination: Path, timeout_sec: float = 30,
                       max_bytes: int = MAX_BYTES, *, progress: Optional[Dict] = None) -> Path:
    """Download a complete processed table within 200 MB; never accept raw archives."""
    _validate_url(url)
    _validate_budget(timeout_sec, max_bytes)
    output = Path(destination).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite processed table: {output}")
    start, size = time.monotonic(), 0
    digest = hashlib.sha256()
    with TemporaryDirectory(prefix=".download-", dir=output.parent) as temporary:
        partial = Path(temporary) / "table.part"
        with _response(url, timeout_sec) as response:
            declared = response.headers.get("Content-Length")
            declared = int(declared) if declared else None
            if declared is not None and (declared <= 0 or declared > max_bytes):
                raise ValueError("Processed table exceeds download byte budget or is empty")
            with partial.open("wb") as handle:
                while True:
                    if time.monotonic() - start > timeout_sec:
                        raise TimeoutError("Processed-table total download timeout")
                    remaining = max_bytes - size
                    if remaining == 0:
                        if declared == size:
                            break
                        raise ValueError("Processed table reached download byte budget before verified EOF")
                    chunk = response.read(min(64 * 1024, remaining))
                    size += len(chunk)
                    digest.update(chunk)
                    if progress is not None:
                        progress.update(bytes_received=size, bytes_read=size,
                                        attempted_source_sha256=digest.hexdigest())
                    if time.monotonic() - start > timeout_sec:
                        raise TimeoutError("Processed-table total download timeout")
                    if not chunk:
                        break
                    if size > max_bytes:
                        raise ValueError("Processed table exceeds download byte budget")
                    handle.write(chunk)
            if declared is not None and size != declared:
                raise OSError("Incomplete processed-table download")
        if not size:
            raise ValueError("Empty processed table")
        partial.replace(output)
    return output


class _BoundedSource:
    """Budget reads before requesting bytes; retain a digest of exactly that prefix."""
    def __init__(self, handle, max_bytes, timeout_sec, stats, network=False):
        self.handle, self.max_bytes, self.timeout_sec = handle, max_bytes, timeout_sec
        self.stats, self.network = stats, network
        self.started, self.digest = time.monotonic(), hashlib.sha256()
        self.complete = False

    def readline(self, maximum):
        if time.monotonic() - self.started > self.timeout_sec:
            raise TimeoutError("Processed-table total subset timeout")
        remaining = self.max_bytes - self.stats["bytes_read"]
        if remaining <= 0:
            raise ValueError("Processed subset reached byte budget before enough complete rows")
        line = self.handle.readline(min(maximum + 1, remaining))
        self.stats["bytes_read"] += len(line)
        if self.network:
            self.stats["bytes_received"] += len(line)
        self.digest.update(line)
        self.stats["attempted_source_sha256"] = self.digest.hexdigest()
        if time.monotonic() - self.started > self.timeout_sec:
            raise TimeoutError("Processed-table total subset timeout")
        if len(line) > maximum:
            raise ValueError("Processed header or line exceeds bounded line budget")
        if len(line) == remaining and not line.endswith(b"\n"):
            raise ValueError("Processed subset byte budget ended inside a line")
        if not line:
            self.complete = True
        return line


def _line(handle, maximum=MAX_LINE_BYTES):
    # _BoundedSource already applies the aggregate budget; local full conversion
    # still bounds every row and never asks pandas to load the expression table.
    raw = handle.readline(maximum if isinstance(handle, _BoundedSource) else maximum + 1)
    if len(raw) > maximum:
        raise ValueError("Processed header or line exceeds bounded line budget")
    return raw.decode("utf-8-sig")


def _validate_names(names, kind):
    if not names or len(names) != len(set(names)) or any(not name.strip() for name in names):
        raise ValueError(f"Expression {kind} labels must be unique and nonempty")


def _header(handle):
    header = _line(handle, MAX_HEADER_BYTES)
    parser = _row_parser(header)
    columns, first = parser(header), parser(_line(handle))
    # R write.table may omit the gene-ID header, leaving one fewer header field.
    samples = columns if len(first) == len(columns) + 1 else columns[1:]
    _validate_names(samples, "sample")
    if len(first) != len(samples) + 1:
        raise ValueError("Processed text header and first row do not align")
    return parser, samples, first


def _rows(handle, parser, first, width):
    row = first
    while row:
        if len(row) != width + 1:
            raise ValueError("Processed text row has unexpected number of columns")
        yield row
        line = _line(handle)
        while line and not line.strip():
            line = _line(handle)
        row = parser(line) if line else None


def _metadata_from_labels(samples: list, embryonic_only: bool) -> pd.DataFrame:
    rows = []
    pattern = re.compile(r"^(?P<organ>.+)\.(?P<stage>[eEpP]\d+(?:\.\d+)?)\.(?P<replicate>\d+)$")
    for sample in samples:
        match = pattern.fullmatch(sample)
        if match is None:
            raise ValueError("Unrecognized processed sample label; supply standardized metadata_csv")
        stage = match["stage"]
        if stage.lower().startswith("p"):
            if embryonic_only:
                continue
            raise ValueError("Postnatal stages require explicit metadata on a common time axis")
        rows.append({"sample_id": sample, "embryonic_age": "E" + stage[1:],
                     "organ": match["organ"].lower(), "batch": "unknown",
                     "specimen_id": sample,
                     "specimen_provenance": "processed-column label; independent animal identity unverified"})
    if not rows:
        raise ValueError("No embryonic samples found")
    return pd.DataFrame(rows)


def _metadata(samples, metadata_csv, embryonic_only, subset=False):
    if metadata_csv is None:
        frame = _metadata_from_labels(samples, embryonic_only)
    else:
        path = Path(metadata_csv).expanduser().resolve()
        if path.stat().st_size > 10_000_000:
            raise ValueError("Metadata exceeds 10 MB budget")
        frame = pd.read_csv(path, dtype={"sample_id": str}, keep_default_na=False)
        required = {"sample_id", "embryonic_age", "organ", "batch"}
        if not required <= set(frame.columns):
            raise ValueError("Metadata requires sample_id, embryonic_age, organ and batch")
        _validate_names(frame.sample_id.tolist(), "metadata sample")
        if any(not str(value).strip() for value in frame[list(required)].to_numpy().flat):
            raise ValueError("Metadata required fields cannot be empty")
        if set(samples) != set(frame.sample_id):
            raise ValueError("Metadata and expression sample IDs must match exactly")
        if subset and embryonic_only:
            frame = frame[frame.embryonic_age.astype(str).str.fullmatch(r"[eE]\d+(?:\.\d+)?")].copy()
        if "specimen_id" not in frame:
            frame["specimen_id"] = "unknown"
            frame["specimen_provenance"] = "source metadata does not identify independent specimens"
    available = set(frame.sample_id)
    selected = [sample for sample in samples if sample in available]
    if not selected:
        raise ValueError("No eligible embryonic samples found")
    frame = frame.set_index("sample_id").loc[selected].reset_index()
    frame["numeric_time"] = parse_stages(frame.embryonic_age)
    return frame


def _values(row, indices):
    values = np.asarray([row[index + 1] for index in indices], dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("Processed expression must be finite")
    return values


def _promote_files(staging, output):
    files = list(staging.iterdir())
    for path in files:
        if (output / path.name).exists():
            raise FileExistsError(f"Refusing to overwrite existing artifact: {output / path.name}")
    for path in files:
        path.replace(output / path.name)


def convert_processed(table_path: Path, metadata_csv: Optional[Path], output_dir: Path,
                      embryonic_only: bool = True) -> Dict[str, str]:
    """Two-pass, bounded-row conversion; failed conversions never publish a store."""
    table, output = Path(table_path).resolve(), Path(output_dir).expanduser().resolve()
    if (output / "processed_counts.zarr").exists():
        raise FileExistsError(f"Refusing to overwrite processed store: {output}")
    with table.open("rb") as handle:
        parser, samples, first = _header(handle)
        metadata = _metadata(samples, metadata_csv, embryonic_only)
        selected = metadata.sample_id.tolist()
        indices = [samples.index(sample) for sample in selected]
        genes = [row[0] for row in _rows(handle, parser, first, len(samples))]
    _validate_names(genes, "gene")
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".convert-", dir=output) as temporary:
        staging = Path(temporary)
        compressor = Blosc(cname="zstd", clevel=3, shuffle=Blosc.BITSHUFFLE)
        group = zarr.open_group(str(staging / "processed_counts.zarr"), mode="w")
        array = group.create_dataset("expression", shape=(len(genes), len(selected)), dtype="f8",
                                     chunks=(min(500, len(genes)), min(50, len(selected))), compressor=compressor)
        group.create_dataset("gene_names", data=np.asarray(genes, dtype=str), compressor=compressor)
        group.create_dataset("sample_names", data=np.asarray(selected, dtype=str), compressor=compressor)
        with table.open("rb") as handle:
            parser, current_samples, first = _header(handle)
            if current_samples != samples:
                raise ValueError("Processed table changed during conversion")
            offset, block = 0, []
            for row in _rows(handle, parser, first, len(samples)):
                if offset >= len(genes) or row[0] != genes[offset]:
                    raise ValueError("Processed genes changed during conversion")
                block.append(_values(row, indices))
                offset += 1
                if len(block) == 500:
                    array[offset - len(block):offset, :] = np.asarray(block)
                    block.clear()
            if block:
                array[offset - len(block):offset, :] = np.asarray(block)
            if offset != len(genes):
                raise ValueError("Processed table truncated during conversion")
        metadata.to_csv(staging / "processed_metadata.csv", index=False)
        _promote_files(staging, output)
    return {"zarr": str(output / "processed_counts.zarr"), "metadata": str(output / "processed_metadata.csv")}


def _write_subset(handle, staging, metadata_csv, max_genes, max_samples, max_bytes):
    parser, samples, first = _header(handle)
    metadata = _metadata(samples, metadata_csv, True, subset=True).iloc[:max_samples].copy()
    selected = metadata.sample_id.tolist()
    indices = [samples.index(sample) for sample in selected]
    destination = staging / "processed_subset.tsv"
    genes, persisted = [], 0
    with destination.open("wb") as output:
        def write(row):
            nonlocal persisted
            buffer = io.StringIO(newline="")
            csv.writer(buffer, delimiter="\t", lineterminator="\n").writerow(row)
            encoded = buffer.getvalue().encode("utf-8")
            if persisted + len(encoded) > max_bytes:
                raise ValueError("Processed subset exceeds persisted byte budget")
            output.write(encoded)
            persisted += len(encoded)
        write(["gene_id", *selected])
        for row in _rows(handle, parser, first, len(samples)):
            if not row[0].strip() or row[0] in genes:
                raise ValueError("Expression gene labels must be unique and nonempty")
            _values(row, indices)
            genes.append(row[0])
            write([row[0], *[row[index + 1] for index in indices]])
            if len(genes) == max_genes:
                break  # Do not advance the generator into an unread response tail.
    metadata_path = staging / "selected_metadata.csv"
    metadata.to_csv(metadata_path, index=False)
    result = convert_processed(destination, metadata_path, staging)
    metadata_path.unlink()
    result.update(table=str(destination), selected_genes=genes, selected_samples=selected,
                  shape=[len(genes), len(selected)], subset_sha256=sha256_file(destination))
    return result


def _synthetic(staging, subset, max_genes, max_samples):
    if not subset:
        result = generate_synthetic(staging)
        counts, _ = make_dataset()
    else:
        counts, metadata = make_dataset()
        counts = counts.iloc[:max_genes, :max_samples]
        metadata = metadata.set_index("sample_id").loc[counts.columns].reset_index()
        path = staging / "synthetic_counts.zarr"
        store = ChunkedExpressionStore.from_dataframe_to_zarr(counts, path)
        if hasattr(store, "close"):
            store.close()
        metadata.to_csv(staging / "synthetic_metadata.csv", index=False)
        result = {"zarr": str(path), "metadata": str(staging / "synthetic_metadata.csv")}
    result.update(shape=list(counts.shape), selected_genes=counts.index.tolist(), selected_samples=counts.columns.tolist())
    return result


def _publish(staging, output, result, max_bytes):
    for key, value in list(result.items()):
        if isinstance(value, str) and value.startswith(str(staging) + "/"):
            result[key] = str(output / Path(value).relative_to(staging))
    result["bytes_persisted"] = sum(path.stat().st_size for path in staging.rglob("*") if path.is_file())
    result["bytes_persisted_scope"] = "new data artifacts, excluding this provenance sidecar"
    payload = json.dumps(result, indent=2, allow_nan=False) + "\n"
    if result["bytes_persisted"] + len(payload.encode()) > max_bytes:
        raise ValueError("Output artifacts and provenance exceed persisted byte budget")
    (staging / "provenance.json").write_text(payload)
    _promote_files(staging, output)
    return result


def ingest_real_data(output_dir: Path = DATA_DIRECTORY, timeout_sec: float = 30,
                     url: str = PROCESSED_URL, metadata_csv: Optional[Path] = None, *,
                     subset: bool = False, max_genes: int = 200, max_samples: int = 60,
                     max_download_mb: float = 200, input_table: Optional[Path] = None,
                     offline: bool = False) -> Dict:
    """Use bounded public processed data or an explicitly marked seeded fallback.

    --input-table reads an existing absolute path without downloading or changing
    it. --offline deliberately generates synthetic data. Full mode retains its
    original API and 1000-by-12 synthetic fallback; subset limits are maxima.
    """
    if (isinstance(max_download_mb, bool) or not math.isfinite(max_download_mb)
            or not 0 < max_download_mb <= 200):
        raise ValueError("Download budget must be finite and positive, at most 200 MB")
    max_bytes = int(max_download_mb * 1_000_000)
    _validate_budget(timeout_sec, max_bytes)
    _validate_url(url)
    if any(isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0 for limit in (max_genes, max_samples)):
        raise ValueError("Gene and sample limits must be positive integers")
    if subset and max_genes * max_samples * 8 > 50_000_000:
        raise ValueError("Requested subset exceeds 50 MB dense-memory budget")
    if input_table is not None and not Path(input_table).expanduser().is_absolute():
        raise ValueError("--input-table requires an absolute processed table path")
    if input_table is not None:
        input_table = Path(input_table).expanduser().resolve()
    if offline and input_table is not None:
        raise ValueError("Use --input-table for local public data or --offline for synthetic fallback")
    output = Path(output_dir).expanduser().resolve()
    protected = ("provenance.json", "processed_counts.zarr", "processed_metadata.csv", "processed_subset.tsv",
                 "synthetic_counts.zarr", "synthetic_metadata.csv", "synthetic_counts.csv", "synthetic_counts.h5")
    if any((output / name).exists() for name in protected):
        raise FileExistsError("Refusing to overwrite an existing ingest; choose a new output directory")
    output.mkdir(parents=True, exist_ok=True)
    stats = {"bytes_received": 0, "bytes_read": 0}
    common = {"requested_url": url, "requested_limits": {"subset": subset, "max_genes": max_genes if subset else None,
              "max_samples": max_samples if subset else None, "max_download_mb": max_download_mb,
              "max_bytes": max_bytes, "timeout_sec": timeout_sec},
              "sampling_method": "first genes and first eligible embryonic samples in source order; not representative" if subset else "all genes and eligible samples",
              "byte_accounting": "response-body bytes returned to client; excludes HTTP/TLS and transport buffering",
              "chronology_kind": "collection_stage_reference", "pseudotime_truth_available": False,
              "causal_network_truth_available": False, "clinical_scope": "nonclinical software benchmark"}
    try:
        if offline:
            raise OSError("Offline mode requested; using seeded synthetic fallback")
        with TemporaryDirectory(prefix=".ingest-", dir=output) as temporary:
            staging = Path(temporary)
            if subset:
                network = input_table is None
                source = _response(url, timeout_sec) if network else Path(input_table).open("rb")
                with source:
                    if network:
                        declared = source.headers.get("Content-Length")
                        stats["source_declared_bytes"] = int(declared) if declared else None
                    else:
                        stats["source_file_bytes"] = Path(input_table).stat().st_size
                    reader = _BoundedSource(source, max_bytes, timeout_sec, stats, network)
                    result = _write_subset(reader, staging, metadata_csv, max_genes, max_samples, max_bytes)
                    if (network and reader.complete and stats["source_declared_bytes"] is not None
                            and stats["bytes_read"] != stats["source_declared_bytes"]):
                        raise OSError("Incomplete processed-table download")
                result.update(source_sha256=reader.digest.hexdigest(),
                              source_sha256_scope="full_file" if reader.complete else "partial_stream" if network else "partial_file")
            else:
                table = (Path(input_table).resolve() if input_table is not None else
                         download_processed(url, staging / "Mouse.RPKM.txt", timeout_sec=timeout_sec,
                                            max_bytes=max_bytes, progress=stats))
                if table.stat().st_size > max_bytes:
                    raise ValueError("Processed table exceeds input byte budget")
                stats.update(bytes_received=table.stat().st_size if input_table is None else 0, bytes_read=table.stat().st_size)
                result = convert_processed(table, metadata_csv, staging)
                with ChunkedExpressionStore(result["zarr"]) as store:
                    result.update(selected_genes=store.gene_names, selected_samples=store.sample_names,
                                  shape=[len(store.gene_names), len(store.sample_names)])
                result.update(source_sha256=sha256_file(table), source_sha256_scope="full_file")
            stats.pop("attempted_source_sha256", None)
            result.update(common, **stats, source="ArrayExpress_processed", source_kind="public_processed",
                          url=url, input_table=str(Path(input_table).resolve()) if input_table is not None else None,
                          expression_kind="continuous", fallback_reason=None,
                          batch_provenance="provided" if metadata_csv else "unknown",
                          selection="embryonic samples" if subset or metadata_csv is None else "provided metadata")
            return _publish(staging, output, result, max_bytes)
    except (OSError, TimeoutError, ValueError, UnicodeError, csv.Error, HTTPException, pd.errors.ParserError) as error:
        LOGGER.warning("Processed ingestion failed (%s); using seeded synthetic data", error)
        with TemporaryDirectory(prefix=".ingest-", dir=output) as temporary:
            staging = Path(temporary)
            result = _synthetic(staging, subset, max_genes, max_samples)
            result.update(common, **stats, source="synthetic_fallback", source_kind="synthetic",
                          expression_kind="count", source_sha256=None, source_sha256_scope="unavailable",
                          fallback_reason=f"{type(error).__name__}: {error}",
                          sampling_method="seed 42 synthetic generator; first requested rows/columns when subset enabled; not public observations")
            return _publish(staging, output, result, max_bytes)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DATA_DIRECTORY)
    parser.add_argument("--timeout-sec", type=float, default=30)
    parser.add_argument("--url", default=PROCESSED_URL)
    parser.add_argument("--metadata-csv", type=Path)
    parser.add_argument("--subset", action="store_true", help="Keep first 200 genes and at most 60 embryonic samples; not representative sampling")
    parser.add_argument("--max-genes", type=int, default=200, help="Maximum genes in --subset mode (default 200)")
    parser.add_argument("--max-samples", type=int, default=60, help="Maximum embryonic samples in --subset mode (default 60)")
    parser.add_argument("--max-download-mb", type=float, default=200, help="Maximum read and persisted bytes in decimal MB; hard cap 200")
    parser.add_argument("--input-table", type=Path, help="Absolute path to a local processed text table; no download")
    parser.add_argument("--offline", action="store_true", help="Explicitly generate seeded synthetic fallback without network access")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        result = ingest_real_data(**vars(args))
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
