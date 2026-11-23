"""Typed Parquet transport for a complete governed population contrast family."""

import math
from pathlib import Path

from devbench.core.population_contract import canonical_bytes, content_sha256
from devharness_core.provenance import hash_artifact

_STRING = ("gene_id", "stage", "contrast_id", "estimate_status", "failure_code")
_NUMERIC = (
    "log2_fold_change",
    "standard_error",
    "confidence_interval_lower",
    "confidence_interval_upper",
    "p_value",
    "q_value",
)
_CHUNK_ROWS = 4096
_MAX_BYTES = 95_000_000
_MAX_ROWS = 2_000_000
_FORMAT = "devbench.scrna-population-contrast/2"


def _schema():
    import pyarrow as pa

    return pa.schema(
        [(name, pa.string(), name == "failure_code") for name in _STRING]
        + [(name, pa.float64()) for name in _NUMERIC]
    )


def _metadata(result):
    family = result["testing_family"]
    return canonical_bytes(
        {
            "format": _FORMAT,
            "source_sha256": result["source_sha256"],
            "specification_sha256": result["specification_sha256"],
            "design_sha256": result["design"]["design_sha256"],
            "family": family,
        }
    )


def write_population_family(destination, rows, result):
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = Path(destination)
    if (
        not path.is_absolute()
        or path.parent.resolve() != path.parent
        or path.suffix != ".parquet"
        or path.exists()
        or path.is_symlink()
    ):
        raise ValueError(
            "Population contrast requires a fresh canonical Parquet output"
        )
    if not 0 < len(rows) <= _MAX_ROWS:
        raise ValueError("Complete population family exceeds the binary row bound")
    schema = _schema().with_metadata({b"devbench.population": _metadata(result)})
    with (
        path.open("xb") as stream,
        pq.ParquetWriter(stream, schema, compression="zstd") as writer,
    ):
        for start in range(0, len(rows), _CHUNK_ROWS):
            writer.write_table(
                pa.Table.from_pylist(rows[start : start + _CHUNK_ROWS], schema=schema)
            )
    return _artifact(path)


def _artifact(path):
    artifact = hash_artifact(path, max_bytes=_MAX_BYTES, max_entries=1)
    return {key: artifact[key] for key in ("sha256", "size_bytes")}


def verify_population_family(path, result):
    import pyarrow.parquet as pq

    if _artifact(path) != result["result_artifact"]:
        raise ValueError("Population Parquet digest differs from its summary")
    with pq.ParquetFile(path) as reader:
        family = result["testing_family"]
        if (
            reader.schema_arrow != _schema()
            or (reader.schema_arrow.metadata or {}).get(b"devbench.population")
            != _metadata(result)
            or reader.metadata.num_rows != family["attempted_n"]
            or not 0 < reader.metadata.num_rows <= _MAX_ROWS
        ):
            raise ValueError(
                "Population Parquet schema, footer or row count differs from its family"
            )
        _verify_rows(reader, family, result)


def _verify_rows(reader, family, result):
    genes = []
    stages = family["stages"]
    contrast_ids = [row["contrast_id"] for row in result["design"]["contrasts"]]
    totals = dict.fromkeys(("estimated", "failed", "refused", "not_estimable"), 0)
    offset = 0
    for batch in reader.iter_batches(batch_size=_CHUNK_ROWS):
        for row in batch.to_pylist():
            index = offset % len(stages)
            gene = row["gene_id"]
            if index == 0:
                genes.append(gene)
            if (
                gene != genes[-1]
                or row["stage"] != stages[index]
                or row["contrast_id"] != contrast_ids[index]
            ):
                raise ValueError("Population Parquet gene-by-stage inventory changed")
            status = row["estimate_status"]
            if status not in totals or not _valid_row(row):
                raise ValueError(
                    "Population Parquet estimate or standard BH family changed"
                )
            totals[status] += 1
            offset += 1
    if (
        len(genes) != len(set(genes))
        or content_sha256(genes) != family["feature_ids_sha256"]
    ):
        raise ValueError("Population Parquet feature inventory changed")
    summary = result["summary"]
    if any(
        count != summary[status + "_hypothesis_count"]
        for status, count in totals.items()
    ):
        raise ValueError("Population Parquet statuses differ from its summary")


def _valid_row(row):
    if row["estimate_status"] != "estimated":
        return bool(row["failure_code"]) and all(row[key] is None for key in _NUMERIC)
    return (
        row["failure_code"] is None
        and all(row[key] is not None and math.isfinite(row[key]) for key in _NUMERIC)
        and 0 <= row["p_value"] <= row["q_value"] <= 1
        and row["standard_error"] > 0
        and row["confidence_interval_lower"]
        <= row["log2_fold_change"]
        <= row["confidence_interval_upper"]
    )


def unavailable_rows(reader, limit):
    result = []
    if limit:
        for batch in reader.iter_batches(batch_size=_CHUNK_ROWS):
            for row in batch.to_pylist():
                if row["estimate_status"] != "estimated":
                    result.append(row)
                    if len(result) == limit:
                        return result
    return result
