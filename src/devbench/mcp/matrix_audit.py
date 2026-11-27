"""Structural matrix diagnostic executed in a pinned RNA image."""

import csv
import gzip
import json
from pathlib import Path


def dimensions(path, fmt):
    if fmt in {"csv", "tsv"}:
        with path.open("rb") as stream:
            compressed = stream.read(2) == b"\x1f\x8b"
        opener = gzip.open if compressed else open
        with opener(path, "rt", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle, delimiter="\t" if fmt == "tsv" else ",")
            columns = len(next(reader)) - 1
            rows = 0
            for line in reader:
                if len(line) != columns + 1:
                    raise ValueError("Ragged matrix")
                rows += 1
        return rows, columns
    if fmt == "h5ad":
        import h5py

        with h5py.File(path, "r") as h5:
            matrix = h5["X"]
            obs, genes = (
                matrix.shape
                if isinstance(matrix, h5py.Dataset)
                else matrix.attrs["shape"]
            )
        return int(genes), int(obs)
    if fmt == "zarr":
        import zarr

        matrix = zarr.open_group(str(path), mode="r")["X"]
        obs, genes = matrix.shape if hasattr(matrix, "shape") else matrix.attrs["shape"]
        return int(genes), int(obs)
    if fmt == "parquet":
        import pyarrow.parquet as pq

        info = pq.ParquetFile(path)
        return info.metadata.num_rows, len(info.schema_arrow) - 1
    raise ValueError("Matrix axes are unavailable for this storage format")


def main():
    (binding,) = json.loads(Path("/inputs/bindings.json").read_bytes()).values()
    features, observations = dimensions(Path(binding["path"]), binding["format"])
    summary = {"genes": features, "observations": observations}
    Path("/outputs/summary.json").write_text(json.dumps(summary, allow_nan=False))


if __name__ == "__main__":
    main()
