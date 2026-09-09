"""Bounded processed-expression ingestion with auditable source manifests.

No raw reads or alignments are accepted. Text is scanned one line at a time;
H5AD dense/CSR/CSC and 10X Matrix Market inputs are subsetted before conversion.
Only selected gene-by-sample blocks enter memory. Public chronology records
collection stages, not a ground-truth continuous pseudotime or causal network.
"""
import csv
import gzip
import hashlib
import json
import re
import shlex
import ssl
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

import certifi
import h5py
import numpy as np
import pandas as pd
import zarr
from numcodecs import Blosc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expression_digest(path: Path) -> str:
    """Hash labels and canonical bounded float64 chunks, independent of compression."""
    from devharness_core.storage import ChunkedExpressionStore
    digest = hashlib.sha256()
    with ChunkedExpressionStore(path) as store:
        digest.update(json.dumps([store.gene_names, store.sample_names], separators=(",", ":")).encode())
        for _, values in store.iter_gene_chunks(chunk_size=64):
            digest.update(np.asarray(values, dtype="<f8").tobytes(order="C"))
    return digest.hexdigest()


def download_verified(url: str, destination: Path, *, max_bytes: int = 200_000_000,
                      timeout_sec: float = 90, expected_sha256: Optional[str] = None) -> dict:
    """Download with per-file and wall-time budgets, hash, and atomic promotion."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or re.search(r"\.(bam|sam|cram|fastq|fq)(\.|$)", parsed.path, re.I):
        raise ValueError("Only HTTPS processed data downloads are permitted")
    if not 0 < max_bytes <= 1_000_000_000 or not 0 < timeout_sec <= 90:
        raise ValueError("Invalid processed download budget (max 1 GB and 90 seconds)")
    output = Path(destination).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        actual_hash = sha256_file(output)
        if output.stat().st_size > max_bytes:
            raise ValueError("Cached file exceeds byte budget")
        if expected_sha256 and actual_hash != expected_sha256:
            raise ValueError("Cached file SHA256 does not match expected digest")
        return {"path": str(output), "url": url, "sha256": actual_hash, "bytes": output.stat().st_size, "cached": True}
    partial = output.with_name(output.name + ".part")
    if partial.exists():
        raise FileExistsError("A partial download already exists")
    started, size = time.monotonic(), 0
    digest = hashlib.sha256()
    try:
        request = Request(url, headers={"User-Agent": "DevBench/0.1 processed-data-provenance"})
        with urlopen(request, timeout=timeout_sec, context=ssl.create_default_context(cafile=certifi.where())) as response:
            final_url = response.geturl() if hasattr(response, "geturl") else url
            final = urlparse(final_url)
            if final.scheme != "https" or re.search(r"\.(bam|sam|cram|fastq|fq)(\.|$)", final.path, re.I):
                raise ValueError("Redirect target must remain HTTPS processed data")
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > max_bytes:
                raise ValueError("Processed file exceeds download byte budget")
            with partial.open("xb") as handle:
                while True:
                    if time.monotonic() - started > timeout_sec:
                        raise TimeoutError("Processed download exceeded wall-time budget")
                    chunk = response.read(min(1024 * 1024, max_bytes + 1))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("Processed file exceeds download byte budget")
                    digest.update(chunk)
                    handle.write(chunk)
            if declared and size != int(declared):
                raise OSError("Incomplete processed download")
        if size == 0:
            raise ValueError("Empty processed file")
        actual_hash = digest.hexdigest()
        if expected_sha256 and actual_hash != expected_sha256:
            raise ValueError("Downloaded SHA256 does not match expected digest")
        partial.replace(output)
        return {"path": str(output), "url": url, "sha256": actual_hash, "bytes": size, "cached": False}
    finally:
        partial.unlink(missing_ok=True)


def discover_biostudies(accession: str, cache_dir: Path) -> dict:
    """Cache official study metadata, including processed-file size constraints."""
    if not re.fullmatch(r"E-MTAB-\d+", accession):
        raise ValueError("Expected an ArrayExpress E-MTAB accession")
    download = download_verified(f"https://www.ebi.ac.uk/biostudies/api/v1/studies/{accession}", Path(cache_dir) / f"{accession}.json", max_bytes=10_000_000)
    study = json.loads(Path(download["path"]).read_text())
    files = []
    def walk(value):
        if isinstance(value, dict):
            if "path" in value and value.get("type") == "file":
                path = value["path"]
                files.append({"path": path, "bytes": value.get("size"), "url": f"https://www.ebi.ac.uk/biostudies/files/{accession}/{quote(path)}",
                              "eligible_under_1gb": isinstance(value.get("size"), int) and value["size"] <= 1_000_000_000,
                              "raw_archive": bool(re.search(r"\.(bam|sam|cram|fastq|fq)(\.|$)", path, re.I))})
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(study)
    return {"accession": accession, "metadata_download": download, "files": files}


def _open_text(path):
    return gzip.open(path, "rt", encoding="utf-8", newline="") if str(path).endswith(".gz") else Path(path).open("r", encoding="utf-8", newline="")


def _row_parser(header: str):
    if "\t" in header:
        return lambda line: next(csv.reader([line], delimiter="\t"))
    if "," in header:
        return lambda line: next(csv.reader([line]))
    return shlex.split  # pandas regex separators do not reliably honor quoted whitespace


def _select(all_names, requested, maximum, kind):
    names = list(map(str, all_names))
    if not names or len(set(names)) != len(names) or any(not x for x in names):
        raise ValueError(f"{kind} labels must be nonempty and unique")
    selected = list(map(str, requested)) if requested is not None else names[:maximum]
    if not selected or len(selected) > maximum or len(set(selected)) != len(selected):
        raise ValueError(f"Invalid {kind} selection or selection budget exceeded")
    lookup = {name: i for i, name in enumerate(names)}
    if any(name not in lookup for name in selected):
        raise ValueError(f"Unknown {kind} selection")
    return selected, np.asarray([lookup[name] for name in selected], dtype=np.int64)


def _metadata(samples, metadata):
    if metadata is None:
        rows = []
        for sample in samples:
            match = re.fullmatch(r"(?P<organ>.+)\.(?P<stage>[eE]\d+(?:\.\d+)?)\.(?P<replicate>\d+)", sample)
            if match is None:
                raise ValueError("Supply specimen metadata; collection stage cannot be inferred safely")
            rows.append({"sample_id": sample, "embryonic_age": "E" + match["stage"][1:], "organ": match["organ"],
                         "batch": "unknown", "specimen_id": sample, "specimen_provenance": "processed-column label; independent animal identity unverified"})
        frame = pd.DataFrame(rows)
    else:
        frame = metadata.copy() if isinstance(metadata, pd.DataFrame) else pd.read_csv(metadata, dtype={"sample_id": str})
        if "sample_id" not in frame or frame.sample_id.duplicated().any() or frame.sample_id.isna().any():
            raise ValueError("Metadata requires unique sample_id values")
        if not set(samples) <= set(frame.sample_id):
            raise ValueError("Selected samples are absent from metadata")
        frame = frame.set_index("sample_id").loc[samples].reset_index()
        if "embryonic_age" not in frame:
            raise ValueError("Metadata requires collection-stage embryonic_age")
        if "batch" not in frame:
            frame["batch"] = "unknown"
        if "specimen_id" not in frame:
            frame["specimen_id"] = "unknown"
            frame["specimen_provenance"] = "source metadata does not identify independent specimens"
    if frame["embryonic_age"].isna().any():
        raise ValueError("Collection stages must be provided, not imputed")
    return frame


def _new_store(output, genes, samples, gene_chunk_size):
    if not isinstance(gene_chunk_size, int) or gene_chunk_size < 1:
        raise ValueError("gene_chunk_size must be positive")
    if len(genes) * len(samples) * 8 > 50_000_000:
        raise ValueError("Selected public subset exceeds the 50 MB dense-memory budget")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    path = output / "processed_counts.zarr"
    if path.exists():
        raise FileExistsError("Refusing to overwrite processed store")
    compressor = Blosc(cname="zstd", clevel=3, shuffle=Blosc.BITSHUFFLE)
    group = zarr.open_group(str(path), mode="w", zarr_version=2)
    group.create_dataset("expression", shape=(len(genes), len(samples)), dtype="f8", chunks=(min(gene_chunk_size, len(genes)), min(64, len(samples))), compressor=compressor, fill_value=0)
    group.create_dataset("gene_names", data=np.asarray(genes, dtype=str), compressor=compressor)
    group.create_dataset("sample_names", data=np.asarray(samples, dtype=str), compressor=compressor)
    return output, path, group


def _finish(source, output, path, genes, samples, metadata, expression_kind, source_accession, source_format, extra=None):
    metadata_path = output / "processed_metadata.csv"
    metadata.to_csv(metadata_path, index=False)
    manifest = {"source_kind": "public_processed", "source_accession": source_accession,
                "source_path": str(Path(source).resolve()), "source_sha256": sha256_file(source), "source_bytes": Path(source).stat().st_size,
                "source_format": source_format, "expression_kind": expression_kind, "chronology_kind": "collection_stage_reference",
                "pseudotime_truth_available": False, "causal_network_truth_available": False,
                "clinical_scope": "nonclinical mouse developmental research; no clinical utility claim",
                "selection": {"genes": genes, "samples": samples}, "shape": [len(genes), len(samples)],
                "metadata_sha256": sha256_file(metadata_path), "expression_sha256": expression_digest(path),
                "specimen_provenance": "preserved supplied metadata; unknown fields remain unknown", **(extra or {})}
    manifest_path = output / "source_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")
    return {"zarr": str(path), "metadata": str(metadata_path), "manifest": str(manifest_path), "source_kind": "public_processed"}


def convert_text_subset(source: Path, output_dir: Path, *, metadata=None, sample_ids=None, gene_ids=None,
                        max_genes=200, max_samples=60, gene_chunk_size=64, expression_kind="continuous", source_accession="E-MTAB-6798") -> dict:
    """Read quoted whitespace, tabular or CSV expression without whole-table loads."""
    source = Path(source).resolve()
    with _open_text(source) as handle:
        header = handle.readline()
        parser = _row_parser(header)
        columns = parser(header)
        # R write.table commonly omits the index header entirely.
        first = parser(handle.readline())
        all_samples = columns if len(first) == len(columns) + 1 else columns[1:]
        if len(first) != len(all_samples) + 1:
            raise ValueError("Processed text header and first row do not align")
        samples, sample_indices = _select(all_samples, sample_ids, max_samples, "sample")
    frame = _metadata(samples, metadata)
    requested = set(gene_ids) if gene_ids is not None else None
    found = []
    with _open_text(source) as handle:
        next(handle)
        for line in handle:
            if not line.strip():
                continue
            row = parser(line)
            if len(row) != len(all_samples) + 1:
                raise ValueError("Processed text row has unexpected number of columns")
            if requested is None and len(found) < max_genes or requested is not None and row[0] in requested:
                found.append(row[0])
                if requested is None and len(found) == max_genes or requested is not None and set(found) == requested:
                    break
    genes, _ = _select(found, gene_ids, max_genes, "gene")
    positions = {gene: i for i, gene in enumerate(genes)}
    output, path, group = _new_store(output_dir, genes, samples, gene_chunk_size)
    count_values = True
    written = set()
    with _open_text(source) as handle:
        next(handle)
        for line in handle:
            if not line.strip():
                continue
            row = parser(line)
            if row[0] not in positions:
                continue
            values = np.asarray([row[i + 1] for i in sample_indices], dtype=float)
            if not np.isfinite(values).all() or np.any(values < 0):
                raise ValueError("Expression must be finite and nonnegative")
            count_values &= bool(np.all(values == np.floor(values)))
            group["expression"][positions[row[0]], :] = values
            written.add(row[0])
            if len(written) == len(genes):
                break
    if expression_kind == "count" and not count_values:
        raise ValueError("Declared count expression contains noninteger values")
    return _finish(source, output, path, genes, samples, frame, expression_kind, source_accession, "quoted_text")


def _h5_labels(group):
    index_key = group.attrs.get("_index", "_index")
    if isinstance(index_key, bytes):
        index_key = index_key.decode()
    if index_key not in group:
        raise ValueError("H5AD axis lacks its declared index")
    return group[index_key].asstr()[:].tolist()


def _sparse_block(matrix, rows, columns, encoding):
    """Read selected sparse outer vectors; never materialize complete X."""
    block = np.zeros((len(rows), len(columns)), dtype=float)
    outer, inner = (rows, columns) if encoding == "csr_matrix" else (columns, rows)
    inner_positions = {int(value): i for i, value in enumerate(inner)}
    for i, index in enumerate(outer):
        bounds = matrix["indptr"][int(index):int(index) + 2]
        start, stop = map(int, bounds)
        # A single dense cell/gene vector can itself be large; read sparse data in blocks.
        for offset in range(start, stop, 65536):
            end = min(offset + 65536, stop)
            indices = matrix["indices"][offset:end]
            values = matrix["data"][offset:end]
            for entry, value in zip(indices, values):
                j = inner_positions.get(int(entry))
                if j is not None:
                    if encoding == "csr_matrix":
                        block[i, j] += value
                    else:
                        block[j, i] += value
    return block


def convert_h5ad_subset(source: Path, output_dir: Path, *, metadata, sample_ids=None, gene_ids=None,
                       max_genes=200, max_samples=60, gene_chunk_size=64, expression_kind="count", source_accession="E-MTAB-6967", matrix_key="X") -> dict:
    source = Path(source).resolve()
    with h5py.File(source, "r") as handle:
        samples, sample_indices = _select(_h5_labels(handle["obs"]), sample_ids, max_samples, "sample")
        genes, gene_indices = _select(_h5_labels(handle["var"]), gene_ids, max_genes, "gene")
        frame = _metadata(samples, metadata)
        matrix = handle[matrix_key]
        encoding = matrix.attrs.get("encoding-type", "dense")
        if isinstance(encoding, bytes):
            encoding = encoding.decode()
        if not isinstance(matrix, h5py.Dataset) and encoding not in ("csr_matrix", "csc_matrix"):
            raise ValueError("H5AD X must be dense, CSR, or CSC")
        output, path, group = _new_store(output_dir, genes, samples, gene_chunk_size)
        for start in range(0, len(genes), gene_chunk_size):
            selected = gene_indices[start:start + gene_chunk_size]
            if isinstance(matrix, h5py.Dataset):
                # h5py requires sorted fancy indices; preserve requested order explicitly.
                order = np.argsort(selected)
                block = np.asarray([matrix[int(row), selected[order]][np.argsort(order)] for row in sample_indices])
            else:
                block = _sparse_block(matrix, sample_indices, selected, encoding)
            if not np.isfinite(block).all() or np.any(block < 0) or expression_kind == "count" and np.any(block != np.floor(block)):
                raise ValueError("Invalid expression values for declared measurement kind")
            group["expression"][start:start + len(selected), :] = block.T
    return _finish(source, output, path, genes, samples, frame, expression_kind, source_accession, "h5ad", {"matrix_key": matrix_key, "sparse_encoding": encoding})


def convert_10x_subset(matrix_path: Path, features_path: Path, barcodes_path: Path, output_dir: Path, *, metadata,
                      sample_ids=None, gene_ids=None, max_genes=200, max_samples=60, gene_chunk_size=64, source_accession="E-MTAB-6967") -> dict:
    """Stream a 10X Matrix Market coordinate matrix, including gzip input."""
    with _open_text(features_path) as handle:
        all_genes = [line.split("\t")[0].strip() for line in handle if line.strip()]
    with _open_text(barcodes_path) as handle:
        all_samples = [line.strip() for line in handle if line.strip()]
    genes, gene_indices = _select(all_genes, gene_ids, max_genes, "gene")
    samples, sample_indices = _select(all_samples, sample_ids, max_samples, "sample")
    frame = _metadata(samples, metadata)
    output, path, group = _new_store(output_dir, genes, samples, gene_chunk_size)
    row_lookup = {int(row): i for i, row in enumerate(gene_indices)}
    column_lookup = {int(col): i for i, col in enumerate(sample_indices)}
    # Bounded chosen subset only (default 200*60 doubles, less than 100 KB).
    block = np.zeros((len(genes), len(samples)))
    with _open_text(matrix_path) as handle:
        header = handle.readline().strip().lower()
        if header not in ("%%matrixmarket matrix coordinate integer general", "%%matrixmarket matrix coordinate real general"):
            raise ValueError("Expected a general coordinate Matrix Market expression matrix")
        line = next(handle)
        while line.startswith("%"):
            line = next(handle)
        n_genes, n_samples, n_entries = map(int, line.split())
        if (n_genes, n_samples) != (len(all_genes), len(all_samples)):
            raise ValueError("10X feature/barcode dimensions do not match the matrix")
        entries = 0
        for line in handle:
            if not line.strip():
                continue
            row, column, value = line.split()
            row, column, value = int(row) - 1, int(column) - 1, float(value)
            if not 0 <= row < n_genes or not 0 <= column < n_samples or not np.isfinite(value) or value < 0 or value != int(value):
                raise ValueError("Invalid 10X count entry")
            entries += 1
            if row in row_lookup and column in column_lookup:
                block[row_lookup[row], column_lookup[column]] += value
        if entries != n_entries:
            raise ValueError("Matrix Market entry count does not match its header")
    group["expression"][:] = block
    return _finish(matrix_path, output, path, genes, samples, frame, "count", source_accession, "10x_matrix_market",
                   {"features_sha256": sha256_file(features_path), "barcodes_sha256": sha256_file(barcodes_path)})
